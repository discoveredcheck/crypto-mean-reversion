#!/bin/bash
docker login --username AWS -p $(aws ecr get-login-password --region eu-west-1) 123456789012.dkr.ecr.eu-west-1.amazonaws.com/binance_server
