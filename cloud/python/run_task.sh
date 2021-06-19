#!/bin/bash
aws ecs run-task --cli-input-json file://task_input.json
