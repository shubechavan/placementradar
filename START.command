#!/bin/bash
echo ""
echo " ================================"
echo "  PlacementRadar — KJSIM"
echo "  Starting server..."
echo " ================================"
echo ""

if ! command -v python3 &> /dev/null; then
    echo " ERROR: Python3 not found. Install from https://python.org"
    exit 1
fi

echo " Checking dependencies..."
pip3 install -r requirements.txt --quiet --exists-action i
echo " Dependencies ready."
echo ""

python3 app.py &
SERVER_PID=$!
sleep 3

if command -v open &> /dev/null; then
    open http://localhost:5000
elif command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:5000
fi

echo " Server running at http://localhost:5000"
echo " Close this window to shut down."
echo ""
wait $SERVER_PID
