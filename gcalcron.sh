#!/bin/bash

start_time=$1
end_time=$2
event_summary=$3
event_location=$4
event_description=$5

echo "start_time: $start_time, end_time: $end_time, event_summary=$event_summary, event_location=$event_location, event_description=$event_description" > /tmp/hoge
