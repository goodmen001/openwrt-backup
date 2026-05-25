"""OpenWrt HTTP客户端模块 - 负责通过LuCI Web API连接和操作OpenWrt路由器"""
import time
import logging
import tempfile
from pathlib import Path
from typing import Tuple, Optional, Dict
from urllib.parse import urljoin

import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

logger = logging.getLogger("openwrt-backup")


class OpenWrtHTTPClient:
    def __init__(self, host: str, username: str, password: str):
        self.host = host
        self.username = username
        self.password = password
        self.session = None
        self.base_url = None
        self.auth_token = None

    def _get_base_url(self) -> str:
        h = self.host.strip()
        if not h.startswith(('http://', 'https://')):
            h = f"http://{h}"
        return h.rstrip('/')

    def login(self) -> Tuple[bool, Optional[str]]:
        try:
            self.base_url = self._get_base_url()
            self.session = requests.Session()
            self.session.verify = False

            retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
            self.session.mount('http://', HTTPAdapter(max_retries=retries))
            self.session.mount('https://', HTTPAdapter(max_retries=retries))
            self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

            ubus_url = urljoin(self.base_url, '/ubus')
            payload = {
                "jsonrpc": "2.0", "id": int(time.time()), "method": "call",
                "params": ["00000000000000000000000000000000", "session", "login",
                           {"username": self.username, "password": self.password}]
            }
            resp = self.session.post(ubus_url, json=payload, timeout=5)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"

            data = resp.json()
            result = data.get('result')
            if not result:
                err = data.get('error', {})
                msg = err.get('message', '未知错误')
                if err.get('code') in (-32002, -1) or 'Invalid' in msg:
                    return False, "用户名或密码错误"
                return False, f"登录失败: {msg}"

            if isinstance(result, list):
                if len(result) >= 2 and isinstance(result[0], (int, float)):
                    if result[0] != 0:
                        return False, f"登录失败，状态码: {result[0]}"
                    result = result[1] if len(result) > 1 else {}
                elif len(result) == 1:
                    result = result[0]
                else:
                    for item in result:
                        if isinstance(item, dict) and item.get('ubus_rpc_session'):
                            result = item
                            break

            if isinstance(result, dict) and result.get('ubus_rpc_session'):
                self.auth_token = result['ubus_rpc_session']
                logger.info("ubus登录成功")
                return True, None
            return False, "登录响应缺少session"

        except requests.exceptions.Timeout:
            return False, "连接超时"
        except requests.exceptions.ConnectionError:
            return False, "无法连接"
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False, f"登录异常: {e}"

    def _rpc(self, ns: str, method: str, params: dict = None) -> Optional[Dict]:
        if not self.session:
            return None
        try:
            sid = self.auth_token or "00000000000000000000000000000000"
            payload = {"jsonrpc": "2.0", "id": int(time.time()), "method": "call",
                       "params": [sid, ns, method, params or {}]}
            resp = self.session.post(urljoin(self.base_url, '/ubus'), json=payload, timeout=5)
            if resp.status_code == 200:
                d = resp.json()
                r = d.get('result')
                if isinstance(r, list):
                    if len(r) >= 2 and isinstance(r[0], (int, float)):
                        if r[0] == 0:
                            return r[1] if isinstance(r[1], dict) else r
                        if r[0] in (-32002, -1, 6):
                            self.auth_token = None
                        return None
                    return r[0] if r else None
                return r
            return None
        except Exception:
            return None

    def download_backup_direct(self, filename: str) -> Tuple[bool, Optional[str], Optional[str]]:
        if not self.session or not self.auth_token:
            return False, "未登录", None
        try:
            tmp = Path(tempfile.gettempdir()) / "openwrt_backup"
            tmp.mkdir(parents=True, exist_ok=True)
            local = tmp / filename

            url = urljoin(self.base_url, '/cgi-bin/cgi-backup')
            headers = {
                'Referer': urljoin(self.base_url, '/cgi-bin/luci/admin/system/flash'),
                'Origin': self.base_url
            }
            resp = self.session.post(url, data={'sessionid': self.auth_token, 'backup': '1'},
                                     headers=headers, timeout=300, stream=True)
            if resp.status_code == 200:
                ct = resp.headers.get('Content-Type', '').lower()
                is_ok = ('octet-stream' in ct or 'x-tar' in ct or 'gzip' in ct or
                         'attachment' in resp.headers.get('Content-Disposition', '').lower() or
                         int(resp.headers.get('Content-Length', '0')) > 1000)
                if is_ok:
                    with open(local, 'wb') as f:
                        for chunk in resp.iter_content(8192):
                            if chunk:
                                f.write(chunk)
                    if local.stat().st_size > 1000:
                        logger.info(f"备份下载成功: {filename}, {local.stat().st_size} 字节")
                        return True, None, str(local)
                    local.unlink()
                    return False, "文件太小", None
                return False, f"非备份响应: {ct}", None
            return False, f"HTTP {resp.status_code}", None
        except Exception as e:
            return False, f"下载异常: {e}", None

    def close(self):
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
            self.auth_token = None
