"""OpenWrt状态获取模块"""
from typing import Optional, Dict, List
from .http_status import OpenWrtHTTPStatus


class OpenWrtStatus:
    def __init__(self, host: str, username: str, password: str):
        self.http_status = OpenWrtHTTPStatus(host, username, password)

    def get_system_status(self) -> Optional[Dict]:
        return self.http_status.get_system_status()

    def get_traffic_stats(self) -> Optional[List[Dict]]:
        return self.http_status.get_traffic_stats()

    def get_plugin_services(self) -> Optional[List[Dict]]:
        return self.http_status.get_plugin_services()
