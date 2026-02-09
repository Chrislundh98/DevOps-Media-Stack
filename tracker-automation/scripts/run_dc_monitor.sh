#!/bin/bash
cd /volume1/automation/trackers
source venv/bin/activate
python3 -m core.digitalcore
deactivate
