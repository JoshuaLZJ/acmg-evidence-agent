#!/bin/bash
#SBATCH --job-name=acmg_finetune
#SBATCH --partition=gpu
#SBATCH --gres=gpu:A100:1
#SBATCH --mem=48G
#SBATCH --cpus-per-task=8
#SBATCH --time=12:00:00
#SBATCH --output=logs/finetune_%j.log
#SBATCH --error=logs/finetune_%j.err

mkdir -p logs checkpoints

source /usr/local/anaconda/5.1.0-Python3.6-gcc5/etc/profile.d/conda.sh
conda activate finetune

python scripts/finetune.py \
    --train data/train.jsonl \
    --val   data/val.jsonl \
    --out   checkpoints/acmg-llama3-8b \
    --epochs 3