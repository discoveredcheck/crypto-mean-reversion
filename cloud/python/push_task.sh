#!/bin/bash
docker tag $(docker images -q binance_server) 123456789012.dkr.ecr.eu-west-1.amazonaws.com/binance_server
docker push 123456789012.dkr.ecr.eu-west-1.amazonaws.com/binance_server
