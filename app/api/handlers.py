import os
import time
import threading
import tempfile
import logging
from pathlib import Path

from ..config import BACKUP_DIR
from ..openwrt.status import OpenWrtStatus

logger = logging.getLogger("openwrt-backup")


class APIHandler:
    def __init__(self, ctx):
        self.ctx = ctx

    # ── Config ──
    def get_config(self):
        return self.ctx.get_config()

    def save_config(self, data: dict):
        cfg = self.ctx.get_config()
        cfg.update(data)
        self.ctx.save_config(cfg)
        for k, v in data.items():
            setattr(self.ctx, f"_{k}", v)
        self.ctx.scheduler_manager.setup_scheduler()
        return {"success": True, "message": "配置已保存"}

    # ── Status ──
    def get_status(self):
        next_run = None
        if self.ctx._scheduler:
            job = self.ctx._scheduler.get_job("openwrt_backup_cron")
            if job and job.next_run_time:
                next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "enabled": self.ctx._enabled if hasattr(self.ctx, "_enabled") else False,
            "backup_activity": self.ctx._backup_activity,
            "cron": getattr(self.ctx, "_cron", "0 3 * * *"),
            "next_run_time": next_run,
        }

    # ── Dashboard ──
    def get_dashboard_data(self):
        bh = self.ctx.history_handler.load_backup_history()
        ab = self.ctx.backup_manager.get_available_backups()
        return {
            "backup_stats": {
                "total": len(bh),
                "successful": sum(1 for x in bh if x.get("success")),
                "failed": sum(1 for x in bh if not x.get("success")),
            },
            "available_backups": {
                "local": sum(1 for x in ab if x["source"] == "本地备份"),
                "webdav": sum(1 for x in ab if x["source"] == "WebDAV备份"),
                "total": len(ab),
            },
            "status": {
                "backup_activity": self.ctx._backup_activity,
                "running": self.ctx._running,
            },
        }

    # ── History ──
    def get_backup_history(self):
        return self.ctx.history_handler.load_backup_history() or []

    def clear_history(self):
        self.ctx.history_handler.clear_all_history()
        return {"success": True, "message": "历史已清理"}

    # ── Backup actions ──
    def run_backup(self):
        if not getattr(self.ctx, "_host", "") or not getattr(self.ctx, "_username", ""):
            return {"success": False, "message": "OpenWRT配置不完整，请先在配置页填写主机和SSH信息"}
        if not getattr(self.ctx, "_password", ""):
            return {"success": False, "message": "SSH密码未配置"}
        lock = getattr(self.ctx, "_lock", None)
        if lock and lock.locked():
            return {"success": False, "message": "备份任务正在执行中，请等待完成"}
        g_lock = getattr(self.ctx, "_global_task_lock", None)
        if g_lock and g_lock.locked():
            return {"success": False, "message": "其他任务正在执行中，请等待完成"}
        threading.Thread(target=self.ctx.backup_executor.run_backup_job, daemon=True).start()
        return {"success": True, "message": "备份任务已启动"}

    def get_available_backups(self):
        return self.ctx.backup_manager.get_available_backups() or []

    def delete_backup(self, data: dict):
        fn = data.get("filename", "")
        src = data.get("source", "本地备份")
        if not fn:
            return {"success": False, "message": "缺少文件名"}
        if src == "本地备份":
            bp = Path(getattr(self.ctx, "_backup_path", str(BACKUP_DIR)))
            fp = bp / fn
            if not fp.is_file() or not str(fp.resolve()).startswith(str(bp.resolve())):
                return {"success": False, "message": "文件不存在"}
            os.remove(fp)
            return {"success": True, "message": f"已删除: {fn}"}
        elif src == "WebDAV备份":
            from ..webdav.webdav_client import WebDAVClient
            client = WebDAVClient(
                url=self.ctx._webdav_url, username=self.ctx._webdav_username,
                password=self.ctx._webdav_password, path=self.ctx._webdav_path,
                skip_dir_check=True, logger=logger, plugin_name="OpenWRT-Backup",
            )
            ok, err = client.delete_file(fn)
            client.close()
            if ok:
                return {"success": True, "message": f"已删除WebDAV: {fn}"}
            return {"success": False, "message": f"删除失败: {err}"}
        return {"success": False, "message": "不支持的来源"}

    # ── OpenWRT Status ──
    def get_system_status(self):
        host = getattr(self.ctx, "_host", "")
        user = getattr(self.ctx, "_username", "")
        pwd = getattr(self.ctx, "_password", "")
        if not host or not user or not pwd:
            return {"success": False, "message": "OpenWRT未配置"}
        try:
            status = OpenWrtStatus(host, user, pwd)
            result = status.get_system_status()
            return result or {"success": False, "message": "获取状态失败"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_traffic_stats(self):
        host = getattr(self.ctx, "_host", "")
        user = getattr(self.ctx, "_username", "")
        pwd = getattr(self.ctx, "_password", "")
        if not host or not user or not pwd:
            return {"success": False, "message": "OpenWRT未配置"}
        try:
            status = OpenWrtStatus(host, user, pwd)
            result = status.get_traffic_stats()
            return result or []
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_plugin_services(self):
        host = getattr(self.ctx, "_host", "")
        user = getattr(self.ctx, "_username", "")
        pwd = getattr(self.ctx, "_password", "")
        if not host or not user or not pwd:
            return {"success": False, "message": "OpenWRT未配置"}
        try:
            status = OpenWrtStatus(host, user, pwd)
            result = status.get_plugin_services()
            return result or []
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ── Other ──
    def stop_all_tasks(self):
        stopped = []
        for lock_name in ("_lock", "_global_task_lock"):
            lock = getattr(self.ctx, lock_name, None)
            if lock and lock.locked():
                try:
                    lock.release()
                    stopped.append(lock_name)
                except RuntimeError:
                    pass
        self.ctx._running = False
        self.ctx._backup_activity = "空闲"
        msg = f"已停止: {', '.join(stopped)}" if stopped else "无运行中的任务"
        return {"success": True, "message": msg}

    def download_backup(self, filename: str, source: str = "本地备份"):
        if source == "本地备份":
            bp = Path(getattr(self.ctx, "_backup_path", str(BACKUP_DIR)))
            fp = bp / filename
            if fp.is_file() and str(fp.resolve()).startswith(str(bp.resolve())):
                return str(fp)
            return None
        elif source == "WebDAV备份":
            tmp = Path(tempfile.gettempdir()) / "openwrt_backup_download"
            tmp.mkdir(parents=True, exist_ok=True)
            dest = str(tmp / filename)
            ok, err = self.ctx.backup_manager.download_from_webdav(filename, dest)
            if ok:
                return dest
            return None
        return None

    def test_notification(self):
        from ..notification.notifications import CHANNEL_DESCRIPTIONS
        channels = getattr(self.ctx, "_notify_channels", {}) or {}
        results = {}
        for ch_name, ch_conf in channels.items():
            if not ch_conf.get("enabled"):
                results[ch_name] = {"sent": False, "error": "未启用"}
                continue
            desc = CHANNEL_DESCRIPTIONS.get(ch_name, {})
            label = desc.get("label", ch_name)
            title = f"{label} 测试通知"
            text = f"这是一条来自 OpenWRT Backup 的测试通知\n如果你收到这条消息，说明通知配置正确 ✅\n\n⏱️ {__import__('time').time()}"
            try:
                method = getattr(self.ctx.notification_handler, f"_send_{ch_name}", None)
                if method:
                    method(title, text, ch_conf)
                    results[ch_name] = {"sent": True, "error": None}
                else:
                    results[ch_name] = {"sent": False, "error": "未知渠道"}
            except Exception as e:
                results[ch_name] = {"sent": False, "error": str(e)}
        return {"success": True, "results": results}

    def get_token(self):
        return {"api_token": os.environ.get("OPENWRT_API_TOKEN", "openwrt-backup-token")}
