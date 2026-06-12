import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")


def _run_scheduled_sync():
    from app.services.sync_service import run_sync
    logger.info("Sync agendado iniciado.")
    try:
        run_sync("all")
    except Exception as e:
        logger.error(f"Erro no sync agendado: {e}", exc_info=True)


def start_scheduler(hour: int = 7, minute: int = 0):
    _scheduler.remove_all_jobs()
    _scheduler.add_job(
        _run_scheduled_sync,
        CronTrigger(hour=hour, minute=minute),
        id="daily_sync",
        replace_existing=True,
    )
    if not _scheduler.running:
        _scheduler.start()
    logger.info(f"Scheduler iniciado: sync diário às {hour:02d}:{minute:02d}")


def update_schedule(hour: int, minute: int):
    _scheduler.reschedule_job(
        "daily_sync",
        trigger=CronTrigger(hour=hour, minute=minute),
    )
    logger.info(f"Schedule atualizado: sync diário às {hour:02d}:{minute:02d}")


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
