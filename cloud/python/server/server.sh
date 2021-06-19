#!/bin/bash
now=$(date +"%d_%m_%Y_%H_%M_%S")
python3 /home/ec2-user/server/server.py &> /home/ec2-user/server/log 
