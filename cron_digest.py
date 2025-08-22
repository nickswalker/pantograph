#!/usr/bin/env python3
"""
Daily digest cron job for processing new member notifications.
Runs the digest processing function directly. We might fight the other
process for the lock on rare occasions.
"""

import sys
import logging
from app import create_app
from app.utils import process_new_members_digests

def main():
    """Main function to run the digest processing"""
    app = create_app()
    
    with app.app_context():
        try:
            logging.info("Starting daily digest processing...")
            process_new_members_digests()
            logging.info("Daily digest processing completed successfully")
            return 0
        except Exception as e:
            logging.error(f"Error during digest processing: {e}")
            return 1

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    exit_code = main()
    sys.exit(exit_code)