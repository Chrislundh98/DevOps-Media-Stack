#!/bin/bash
cd "$(dirname "$0")/.."
source venv/bin/activate
python3 -m core.dc_reporter
deactivate
