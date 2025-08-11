
#!/usr/bin/env bash
gunicorn app:APP --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 120
