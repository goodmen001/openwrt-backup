import os
import re
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

from ..config import BACKUP_DIR

logger = logging.getLogger("openwrt-backup")


class BackupExecutor:
    def __init__(self, app_ctx):
        self.ctx = app_ctx

    def run_backup_job(self):
        if not self.ctx._lock:
            self.ctx._lock = threading.Lock()
        if not self.ctx._global_task_lock:
            self.ctx._global_task_lock = threading.Lock()

        if not self.ctx._global_task_lock.acquire(blocking=False):
            logger.debug("其他任务执行中，备份跳过")
            return
        if not self.ctx._lock.acquire(blocking=False):
            logger.debug("已有备份执行中，跳过")
            self.ctx._global_task_lock.release()
            return

        entry = {"timestamp": time.time(), "success": False, "filename": None, "message": "开始"}
        self.ctx._backup_activity = "开始"

        try:
            self.ctx._running = True
            logger.info("开始OpenWRT备份任务...")

            host = getattr(self.ctx, "_host", "")
            user = getattr(self.ctx, "_username", "")
            pwd = getattr(self.ctx, "_password", "")
            if not host or not user or not pwd:
                err = "OpenWRT配置不完整"
                logger.error(err)
                self.ctx.notification_handler.send_backup_notification(success=False, message=err, backup_details={})
                entry["message"] = err
                self.ctx.history_handler.save_backup_history_entry(entry)
                return

            bpath = getattr(self.ctx, "_backup_path", str(BACKUP_DIR))
            Path(bpath).mkdir(parents=True, exist_ok=True)

            for i in range(getattr(self.ctx, "_retry_count", 0) + 1):
                ok, err_msg, fname = self._perform_once(host, user, pwd)
                if ok:
                    self.ctx.notification_handler.send_backup_notification(
                        success=True, message="备份成功", filename=fname, backup_details={}
                    )
                    entry.update({"success": True, "filename": fname, "message": "成功"})
                    self.ctx.history_handler.save_backup_history_entry(entry)
                    return
                else:
                    logger.warning(f"第{i+1}次备份失败: {err_msg}")
                    if i < getattr(self.ctx, "_retry_count", 0):
                        time.sleep(getattr(self.ctx, "_retry_interval", 60))

            entry["message"] = f"备份失败: {err_msg}"
            self.ctx.history_handler.save_backup_history_entry(entry)
            self.ctx.notification_handler.send_backup_notification(
                success=False, message=entry["message"], backup_details={}
            )

        except Exception as e:
            logger.error(f"备份主流程异常: {e}")
            entry["message"] = f"异常: {e}"
            self.ctx.history_handler.save_backup_history_entry(entry)
            self.ctx.notification_handler.send_backup_notification(
                success=False, message=entry["message"], backup_details={}
            )
        finally:
            self.ctx._running = False
            self.ctx._backup_activity = "空闲"
            for lock in (self.ctx._lock, self.ctx._global_task_lock):
                if lock and lock.locked():
                    try:
                        lock.release()
                    except RuntimeError:
                        pass

    def _perform_once(self, host: str, username: str, password: str) -> Tuple[bool, Optional[str], Optional[str]]:
        from ..openwrt.client import OpenWrtClient

        client = OpenWrtClient(host, username, password)
        http_client, err = client.connect()
        if err or not http_client:
            return False, f"连接失败: {err}", None

        try:
            ok, err_msg, fn, path = client.create_backup(http_client)
            if not ok or not path:
                return False, f"备份失败: {err_msg}", None

            # Post-process: WebDAV upload, cleanup
            details = self._process_backup(path, fn)

            return True, None, fn
        except Exception as e:
            return False, f"备份异常: {e}", None
        finally:
            try:
                http_client.close()
            except Exception:
                pass

    def _process_backup(self, local_path: str, filename: str) -> Dict[str, Any]:
        details = {
            "local_backup": {"enabled": True, "success": False},
            "webdav_backup": {"enabled": False, "success": False},
        }

        # Local backup: file already exists at local_path
        details["local_backup"]["success"] = True
        details["local_backup"]["path"] = local_path
        details["local_backup"]["filename"] = filename
        logger.info(f"本地备份成功: {filename}")

        # WebDAV upload
        webdav_enabled = getattr(self.ctx, "_enable_webdav", False)
        webdav_url = getattr(self.ctx, "_webdav_url", "")
        if webdav_enabled and webdav_url:
            success, error = self.ctx.backup_manager.upload_to_webdav(local_path, filename)
            details["webdav_backup"] = {
                "enabled": True, "success": success, "filename": filename, "error": error,
            }
            if success:
                logger.info(f"WebDAV备份成功: {filename}")
            else:
                logger.error(f"WebDAV备份失败: {error}")

        # Cleanup local old backups
        self.ctx.backup_manager.cleanup_old_backups()

        # Cleanup WebDAV old backups
        if webdav_enabled and webdav_url:
            self.ctx.backup_manager.cleanup_webdav_backups()

        return details
