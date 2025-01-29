import logging
import os

logging.basicConfig(level=logging.DEBUG if os.environ.get('DEBUG') else logging.INFO)
logger = logging.getLogger('proxy-wol')
