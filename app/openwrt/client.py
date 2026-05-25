"""OpenWrt客户端模块 - 负责通过HTTP API连接和操作OpenWrt路由器"""
from datetime import datetime
from typing import Tuple, Optional
import logging

from .http_client import OpenWrtHTTPClient

logger = logging.getLogger("openwrt-backup")


class OpenWrtClient:
    def __init__(self, host: str, username: str, password: str):
        self.host = host
        self.username = username
        self.password = password
        self.http_client = OpenWrtHTTPClient(host, username, password)

    def connect(self) -> Tuple[Optional[OpenWrtHTTPClient], Optional[str]]:
        if not self.host:
            return None, "未配置OpenWrt地址"
        if not self.username:
            return None, "未配置用户名"
        if not self.password:
            return None, "未配置密码"
        ok, err = self.http_client.login()
        if not ok:
            return None, err
        return self.http_client, None

    def create_backup(self, http_client: OpenWrtHTTPClient) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            fn = f"backup_{ts}.tar.gz"
            logger.info("通过官方接口下载备份...")
            ok, err, path = http_client.download_backup_direct(fn)
            if ok and path:
                return True, None, fn, path
            return False, f"下载备份失败: {err or '未知'}", None, None
        except Exception as e:
            return False, f"创建备份异常: {e}", None, None
