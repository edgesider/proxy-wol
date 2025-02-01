from utils import get_env_bool, get_env_int

_run_agent = get_env_bool('RUN_AGENT', False)
_port = get_env_int('PORT', 4321 if not _run_agent else 4322)

bind = f'0.0.0.0:{_port}'
reuse_port = True
workers = 1
worker_class = 'aiohttp.GunicornWebWorker'
capture_output = True

if _run_agent:
    wsgi_app = 'agent:get_app'
else:
    wsgi_app = 'proxy:main'
    accesslog = '-'
