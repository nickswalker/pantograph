"""One-off backfill: generate thumbnails for images uploaded before thumbnailing.

Safe to run repeatedly — it skips images that already have a thumbnail, so it's
resumable. Deliberately sequential with a small per-image sleep so a large
backlog can't spike CPU/memory and choke the box; run it off-peak.

    python -m app.backfill_thumbnails [--sleep 0.05]
"""

import argparse
import logging
import os
import time

from app import create_app
from app.config import Config
from app.models import Image
from app.utils import thumbnail_basename, generate_thumbnail_from_file


def backfill(sleep_seconds=0.05):
    app = create_app()
    with app.app_context():
        images = Image.query.order_by(Image.upload_time).all()
        total = len(images)
        generated = skipped = missing = failed = 0
        logging.info("Backfilling thumbnails for %d images", total)

        for i, image in enumerate(images, 1):
            src = os.path.join(Config.UPLOAD_FOLDER, image.file_path)
            thumb = os.path.join(
                Config.UPLOAD_FOLDER,
                os.path.dirname(image.file_path),
                Config.THUMBNAIL_DIR,
                thumbnail_basename(os.path.basename(image.file_path)),
            )

            if os.path.exists(thumb):
                skipped += 1
                continue
            if not os.path.exists(src):
                logging.warning("Source missing for image %s: %s", image.id, src)
                missing += 1
                continue

            if generate_thumbnail_from_file(src, thumb):
                generated += 1
            else:
                failed += 1

            if i % 100 == 0:
                logging.info("…%d/%d (generated=%d skipped=%d)", i, total, generated, skipped)
            time.sleep(sleep_seconds)  # throttle so the backlog can't choke the server

        logging.info(
            "Done: %d generated, %d already present, %d source-missing, %d failed (of %d)",
            generated, skipped, missing, failed, total,
        )


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    parser = argparse.ArgumentParser(description="Backfill gallery thumbnails")
    parser.add_argument('--sleep', type=float, default=0.05,
                        help="seconds to sleep between images (throttle)")
    args = parser.parse_args()
    backfill(args.sleep)
