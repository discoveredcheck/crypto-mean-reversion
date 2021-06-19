#!/bin/bash
docker build --rm -t binance_server .
docker tag $(docker images -q binance_server) 123456789012.dkr.ecr.eu-west-1.amazonaws.com/binance_server
