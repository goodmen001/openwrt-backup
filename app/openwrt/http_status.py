"""
OpenWrt HTTP状态获取模块
负责通过HTTP API获取系统状态、流量统计和服务状态
"""
import re
import logging
from typing import Optional, Dict, List

from .http_client import OpenWrtHTTPClient

logger = logging.getLogger("openwrt-backup")


class OpenWrtHTTPStatus:
    """OpenWrt HTTP状态获取类"""

    def __init__(self, host: str, username: str, password: str):
        self.http_client = OpenWrtHTTPClient(host, username, password)

    def _ensure_connected(self) -> bool:
        if not self.http_client.session or not self.http_client.auth_token:
            success, error = self.http_client.login()
            if not success:
                logger.error(f"OpenWrt HTTP连接失败: {error}")
                return False
            return True

        try:
            test_result = self.http_client._rpc('system', 'board', {})
            if test_result is None:
                logger.debug("检测到session可能过期，重新登录...")
                self.http_client.session = None
                self.http_client.auth_token = None
                success, error = self.http_client.login()
                if not success:
                    logger.error(f"HTTP重新连接失败: {error}")
                    return False
        except Exception as e:
            logger.debug(f"连接测试异常，重新登录: {e}")
            self.http_client.session = None
            self.http_client.auth_token = None
            success, error = self.http_client.login()
            if not success:
                logger.error(f"HTTP重新连接失败: {error}")
                return False

        return True

    def get_system_status(self) -> Optional[Dict]:
        if not self._ensure_connected():
            return None

        try:
            status = {}

            result = self.http_client._rpc('system', 'info', {})
            if result:
                if 'uptime' in result:
                    uptime_seconds = result['uptime']
                    days = int(uptime_seconds // 86400)
                    hours = int((uptime_seconds % 86400) // 3600)
                    minutes = int((uptime_seconds % 3600) // 60)
                    if days > 0:
                        status['uptime'] = f"{days}天{hours}小时{minutes}分钟"
                    else:
                        status['uptime'] = f"{hours}小时{minutes}分钟"
                    status['uptime_seconds'] = int(uptime_seconds)

                if 'load' in result:
                    load = result['load']
                    if isinstance(load, list) and len(load) >= 3:
                        load_1min = load[0] / 65536.0 if isinstance(load[0], (int, float)) else float(load[0])
                        load_5min = load[1] / 65536.0 if isinstance(load[1], (int, float)) else float(load[1])
                        load_15min = load[2] / 65536.0 if isinstance(load[2], (int, float)) else float(load[2])

                        status['load_1min'] = f"{load_1min:.2f}".rstrip('0').rstrip('.')
                        status['load_5min'] = f"{load_5min:.2f}".rstrip('0').rstrip('.')
                        status['load_15min'] = f"{load_15min:.2f}".rstrip('0').rstrip('.')

                if 'memory' in result:
                    memory = result['memory']
                    if isinstance(memory, dict):
                        total = memory.get('total', 0)
                        free = memory.get('free', 0)
                        cached = memory.get('cached', 0)
                        buffered = memory.get('buffered', 0)
                        used = total - free - cached - buffered

                        status['memory_total'] = total // (1024 * 1024)
                        status['memory_used'] = used // (1024 * 1024)
                        status['memory_free'] = free // (1024 * 1024)
                        status['memory_usage'] = round((used / total * 100), 1) if total > 0 else 0

                if 'swap' in result:
                    swap = result['swap']
                    if isinstance(swap, dict):
                        swap_total = swap.get('total', 0)
                        swap_free = swap.get('free', 0)
                        swap_used = swap_total - swap_free
                        status['swap_total'] = swap_total // (1024 * 1024)
                        status['swap_used'] = swap_used // (1024 * 1024)
                        status['swap_usage'] = round((swap_used / swap_total * 100), 1) if swap_total > 0 else 0

            cpu_usage_result = self.http_client._rpc('luci', 'getCPUUsage', {})
            if cpu_usage_result and isinstance(cpu_usage_result, dict):
                cpuusage_str = cpu_usage_result.get('cpuusage', '')
                if cpuusage_str:
                    cpu_match = re.search(r'(\d+\.?\d*)%', cpuusage_str)
                    if cpu_match:
                        status['cpu_usage'] = round(float(cpu_match.group(1)), 1)

            luci_version = self.http_client._rpc('luci', 'getVersion', {})
            if luci_version and isinstance(luci_version, dict):
                revision = luci_version.get('revision', '')
                branch = luci_version.get('branch', '')
                if revision and branch:
                    status['version'] = f"{branch} ({revision})"
                elif revision:
                    status['version'] = revision
                elif branch:
                    status['version'] = branch

            board_result = self.http_client._rpc('system', 'board', {})
            if board_result and isinstance(board_result, dict):
                if 'kernel' in board_result:
                    status['kernel'] = board_result['kernel']

                board_name = board_result.get('board_name', '')
                model = board_result.get('model', '')
                system = board_result.get('system', '')

                arch_parts = []
                if board_name:
                    arch_parts.append(board_name)
                if model:
                    arch_parts.append(model)
                if system:
                    arch_parts.append(system)

                if arch_parts:
                    status['architecture'] = ' / '.join(arch_parts)

            temp_result = self.http_client._rpc('luci', 'getTempInfo', {})
            if temp_result and isinstance(temp_result, dict):
                tempinfo_str = temp_result.get('tempinfo', '')
                if tempinfo_str:
                    status['temperature'] = tempinfo_str
                else:
                    status['temperature'] = 'N/A'
            else:
                status['temperature'] = 'N/A'

            return status
        except Exception as e:
            logger.error(f"获取系统状态失败: {e}")
            return None

    def get_traffic_stats(self) -> Optional[List[Dict]]:
        if not self._ensure_connected():
            return None

        try:
            traffic_stats = []

            result = self.http_client._rpc('luci.wrtbwmon', 'get_db_raw', {'protocol': 'ipv4'})
            if result and isinstance(result, dict):
                data_content = result.get('data', '')
                if isinstance(data_content, str):
                    lines = data_content.split('\n')

                    for line in lines:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue

                        parts = line.split(',')
                        if len(parts) >= 9:
                            try:
                                mac = parts[0]
                                ip = parts[1] if parts[1] != 'NA' else ''
                                iface = parts[2]
                                in_bytes = int(parts[5]) if parts[5] else 0
                                out_bytes = int(parts[6]) if parts[6] else 0

                                rx_mb = round(in_bytes / (1024 * 1024), 2)
                                tx_mb = round(out_bytes / (1024 * 1024), 2)

                                device_name = ip if ip else mac

                                traffic_stats.append({
                                    'interface': device_name,
                                    'mac': mac,
                                    'ip': ip,
                                    'iface': iface,
                                    'rx_bytes': in_bytes,
                                    'rx_mb': rx_mb,
                                    'rx_packets': 0,
                                    'tx_bytes': out_bytes,
                                    'tx_mb': tx_mb,
                                    'tx_packets': 0
                                })
                            except (ValueError, IndexError):
                                continue

            traffic_stats.sort(key=lambda x: x['rx_bytes'] + x['tx_bytes'], reverse=True)

            return traffic_stats
        except Exception as e:
            logger.error(f"获取流量统计失败: {e}")
            return None

    def get_plugin_services(self) -> Optional[List[Dict]]:
        if not self._ensure_connected():
            return None

        try:
            plugin_services = []

            plugin_keywords = [
                'lucky', 'nikki', 'nps', 'snmpd', 'turboacc', 'eqos',
                'wrtbwmon', 'design', 'adguard', 'passwall', 'openclash',
                'vssr', 'ssr-plus', 'shadowsocks', 'v2ray', 'xray'
            ]

            rc_result = self.http_client._rpc('rc', 'list', {})
            if rc_result and isinstance(rc_result, dict):
                for service_name, service_info in rc_result.items():
                    if isinstance(service_info, dict):
                        service_name_lower = service_name.lower()
                        is_plugin = any(keyword in service_name_lower for keyword in plugin_keywords)

                        if is_plugin:
                            is_enabled = service_info.get('enabled', False)
                            is_running = service_info.get('running', False)

                            if is_enabled or is_running:
                                plugin_services.append({
                                    'name': service_name,
                                    'enabled': is_enabled,
                                    'running': is_running,
                                    'status': '运行中' if is_running else '已停止'
                                })

                plugin_services.sort(key=lambda x: x['name'])

            return plugin_services
        except Exception as e:
            logger.error(f"获取插件服务状态失败: {e}")
            return None
