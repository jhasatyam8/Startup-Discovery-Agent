import os
import subprocess
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_all")

def run_all():
    processes = []
    try:
        # 1. Start FastAPI Dashboard
        port = os.getenv("PORT", "8000")
        logger.info(f"Starting FastAPI Dashboard on port {port}...")
        # Bind dashboard to 0.0.0.0 for external Render routing
        os.environ["HOST"] = "0.0.0.0"
        p_dash = subprocess.Popen([sys.executable, "main.py", "--start-dashboard"])
        processes.append(p_dash)

        # 2. Start Telegram Bot Polling
        logger.info("Starting Telegram Bot Polling...")
        p_bot = subprocess.Popen([sys.executable, "telegram_bot.py"])
        processes.append(p_bot)

        # 3. Start Standalone Scheduler
        logger.info("Starting Pipeline Scheduler...")
        p_sched = subprocess.Popen([sys.executable, "main.py", "--start-scheduler"])
        processes.append(p_sched)

        logger.info("All processes started successfully. Monitoring...")

        # Monitor processes indefinitely
        while True:
            for p in processes:
                if p.poll() is not None:
                    # One of the critical processes exited
                    logger.error(f"Process {p.args} exited with code {p.returncode}. Restarting container...")
                    raise SystemExit(1)
            time.sleep(5)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down processes...")
        for p in processes:
            try:
                p.terminate()
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        logger.info("All processes terminated.")

if __name__ == "__main__":
    run_all()
