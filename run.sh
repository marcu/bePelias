#!/bin/bash
ACTION=$1

# This scripts run within the bePelias containers
# It starts the API, and run the script building CSV data from BestAddress files. Those files are then imported on the host maching using "build.sh update"

echo "Starting run.sh..."

NB_WORKERS=${NB_WORKERS:-1}

PORT=4001

echo "ACTION: $ACTION"


if [[ $ACTION == "prepare_from_xml"  ]]; then
    echo "Prepare csv from xml"
    
    /convert_xml2csv.sh
    
    python3 /prepare_best_files.py -i /data/in -o /data/
    
elif [[ $ACTION == "run" ]]; then
    echo "Starting service... ($NB_WORKERS workers)" 
    
    gunicorn -w $NB_WORKERS -b 0.0.0.0:$PORT bepelias:app 

    while :; do sleep 3600 ; done
else
    echo "Unknown action $ACTION"
fi
