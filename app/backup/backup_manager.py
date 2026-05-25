import os
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from ..config import BACKUP_DIR

logger = logging.getLogger("openwrt-backup")


class BackupManager:
    def __init__(self, app_ctx):
        self.ctx = app_ctx

    def _get_backup_path(self):
        return Path(getattr(self.ctx, "_backup_path", str(BACKUP_DIR)))

    def cleanup_old_backups(self):
        backup_path = self._get_backup_path()
        keep = getattr(self.ctx, "_keep_backup_num", 5)
        if keep <= 0:
            return
        if not backup_path.is_dir():
            return
        files = []
        for f in backup_path.iterdir():
            if f.is_file() and f.name.endswith(".tar.gz"):
                mt = f.stat().st_mtime
                files.append((mt, f))
        files.sort(key=lambda x: x[0], reverse=True)
        if len(files) > keep:
            for _, f in files[keep:]:
                try:
                    f.unlink()
                    logger.info(f"已删除旧备份: {f.name}")
                except Exception as e:
                    logger.error(f"删除旧备份 {f.name} 失败: {e}")

    def upload_to_webdav(self, local_file_path: str, filename: str) -> Tuple[bool, Optional[str]]:
        if not getattr(self.ctx, "_enable_webdav", False) or not getattr(self.ctx, "_webdav_url", ""):
            return False, "WebDAV未启用"
        try:
            from ..webdav.webdav_client import WebDAVClient
            client = WebDAVClient(
                url=self.ctx._webdav_url,
                username=self.ctx._webdav_username,
                password=self.ctx._webdav_password,
                path=self.ctx._webdav_path,
                skip_dir_check=True,
                logger=logger,
                plugin_name="OpenWRT-Backup",
            )
            success, error = client.upload(local_file_path, filename)
            client.close()
            return success, error
        except Exception as e:
            return False, f"WebDAV上传失败: {e}"

    def cleanup_webdav_backups(self):
        if not getattr(self.ctx, "_enable_webdav", False) or not getattr(self.ctx, "_webdav_url", ""):
            return
        keep = getattr(self.ctx, "_webdav_keep_backup_num", 7)
        if keep <= 0:
            return
        try:
            from ..webdav.webdav_client import WebDAVClient
            client = WebDAVClient(
                url=self.ctx._webdav_url,
                username=self.ctx._webdav_username,
                password=self.ctx._webdav_password,
                path=self.ctx._webdav_path,
                skip_dir_check=True,
                logger=logger,
                plugin_name="OpenWRT-Backup",
            )
            deleted, error = client.cleanup_old_files(keep_count=keep)
            if error:
                logger.error(f"WebDAV清理失败: {error}")
            else:
                logger.info(f"WebDAV清理完成，已删除 {deleted} 个旧文件")
            client.close()
        except Exception as e:
            logger.error(f"WebDAV清理异常: {e}")

    def get_available_backups(self) -> List[Dict[str, Any]]:
        backups = []
        backup_path = self._get_backup_path()
        if backup_path.is_dir():
            for f in backup_path.iterdir():
                if f.is_file() and f.name.endswith(".tar.gz"):
                    try:
                        stat = f.stat()
                        backups.append({
                            "filename": f.name,
                            "path": str(f),
                            "size_mb": stat.st_size / (1024 * 1024),
                            "time_str": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                            "source": "本地备份",
                        })
                    except Exception:
                        pass
        if getattr(self.ctx, "_enable_webdav", False) and getattr(self.ctx, "_webdav_url", ""):
            try:
                from ..webdav.webdav_client import WebDAVClient
                client = WebDAVClient(
                    url=self.ctx._webdav_url,
                    username=self.ctx._webdav_username,
                    password=self.ctx._webdav_password,
                    path=self.ctx._webdav_path,
                    skip_dir_check=True,
                    logger=logger,
                    plugin_name="OpenWRT-Backup",
                )
                files, error = client.list_files()
                if not error:
                    for fi in files:
                        fn = fi.get("filename", "")
                        if fn.lower().endswith(".tar.gz"):
                            ft = fi.get("time")
                            backups.append({
                                "filename": fn,
                                "path": fi.get("href", ""),
                                "size_mb": fi.get("size_mb", 0),
                                "time_str": datetime.fromtimestamp(ft).strftime("%Y-%m-%d %H:%M:%S") if ft else "未知",
                                "source": "WebDAV备份",
                            })
                client.close()
            except Exception:
                pass
        backups.sort(key=lambda x: x.get("time_str", ""), reverse=True)
        return backups

    def download_from_webdav(self, filename: str, local_path: str) -> Tuple[bool, Optional[str]]:
        if not getattr(self.ctx, "_enable_webdav", False) or not getattr(self.ctx, "_webdav_url", ""):
            return False, "WebDAV未启用"
        try:
            from ..webdav.webdav_client import WebDAVClient
            client = WebDAVClient(
                url=self.ctx._webdav_url,
                username=self.ctx._webdav_username,
                password=self.ctx._webdav_password,
                path=self.ctx._webdav_path,
                skip_dir_check=True,
                logger=logger,
                plugin_name="OpenWRT-Backup",
            )
            success, error = client.download(filename, local_path)
            client.close()
            return success, error
        except Exception as e:
            return False, f"WebDAV下载失败: {e}"
