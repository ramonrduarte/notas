import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")

_RETRY_DELAY_MINUTES = 90


def _run_scheduled_sync():
    from app.services.sync_service import run_sync
    logger.info("Sync agendado iniciado.")
    try:
        results = run_sync("all")
        _schedule_retry_if_656(results)
    except Exception as e:
        logger.error(f"Erro no sync agendado: {e}", exc_info=True)


def _schedule_retry_if_656(results: dict):
    tipos_656 = [
        tipo for tipo, r in results.items()
        if r.get("status") == "error" and "656" in (r.get("mensagem") or "")
    ]
    if not tipos_656:
        return
    retry_time = datetime.now() + timedelta(minutes=_RETRY_DELAY_MINUTES)
    logger.warning(
        f"Consumo indevido (656) para {tipos_656}. "
        f"Retry automático agendado para {retry_time.strftime('%H:%M')} ({_RETRY_DELAY_MINUTES} min)"
    )
    _scheduler.add_job(
        _retry_after_656,
        "date",
        run_date=retry_time,
        args=[",".join(tipos_656)],
        id="retry_656",
        replace_existing=True,
    )


def _retry_after_656(tipos_str: str):
    from app.services.sync_service import run_sync
    tipos = tipos_str.split(",")
    logger.info(f"Retry após 656 iniciado para: {tipos}")
    try:
        for tipo in tipos:
            results = run_sync(tipo)
            still_blocked = any(
                "656" in (r.get("mensagem") or "")
                for r in results.values()
                if r.get("status") == "error"
            )
            if still_blocked:
                logger.error(
                    f"Retry {tipo.upper()} ainda bloqueado (656). "
                    "Próxima tentativa no sync agendado do dia seguinte."
                )
    except Exception as e:
        logger.error(f"Erro no retry após 656: {e}", exc_info=True)


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


def get_next_run_times() -> dict:
    result = {}
    try:
        job = _scheduler.get_job("daily_sync")
        if job and job.next_run_time:
            result["next_scheduled"] = job.next_run_time.isoformat()
    except Exception:
        pass
    try:
        job = _scheduler.get_job("retry_656")
        if job and job.next_run_time:
            result["next_retry"] = job.next_run_time.isoformat()
    except Exception:
        pass
    return result


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
