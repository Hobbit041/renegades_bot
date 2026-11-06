import logging

from bot.logging_setup import configure_logging


def test_bounded_rotation_and_scheduler_noise(tmp_path):
    root = logging.getLogger()
    scheduler = logging.getLogger("apscheduler")
    old_handlers, old_level, old_scheduler = root.handlers[:], root.level, scheduler.level
    # Preserve pytest's handlers; basicConfig(force=True) would close them.
    root.handlers = []
    try:
        handler = configure_logging(tmp_path, max_bytes=1024, backup_count=3)
        logging.getLogger("apscheduler.executors.default").info("ROUTINE_HEARTBEAT")
        for _ in range(30):
            logging.getLogger("camp-test").info("x" * 200)
        logging.getLogger("apscheduler.executors.default").error("SCHEDULER_ERROR")
        handler.flush()
        files = list(tmp_path.glob("camp-bot.log*"))
        assert len(files) <= 4
        assert sum(p.stat().st_size for p in files) <= 4 * 1024
        contents = "".join(p.read_text(encoding="utf-8") for p in files)
        assert "ROUTINE_HEARTBEAT" not in contents
        assert "SCHEDULER_ERROR" in contents
    finally:
        for h in root.handlers:
            h.close()
        root.handlers, root.level = old_handlers, old_level
        scheduler.setLevel(old_scheduler)
