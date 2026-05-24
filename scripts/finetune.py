"""
scripts/finetune.py

QLoRA fine-tuning on ACMG variant interpretation exemplars.
Designed for M3 A100 80GB GPU nodes.

Usage (via SLURM — see scripts/submit_finetune.sh):
    python scripts/finetune.py \
        --train data/train.jsonl \
        --val   data/val.jsonl \
        --out   checkpoints/acmg-llama3-8b \
        --epochs 3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import unsloth  # must be first
import torch
from datasets import Dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel

MODEL_NAME   = "unsloth/Meta-Llama-3.1-8B-Instruct"
MAX_SEQ_LEN  = 4096

SYSTEM_PROMPT = (
    "You are a variant interpretation assistant for research prototyping. "
    "Use only retrieved evidence. Separate retrieved facts from inferred conclusions. "
    "Be conservative under uncertainty. All outputs are for research use only "
    "and must not be used for clinical decisions."
)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def format_example(row: dict, tokenizer) -> dict:
    messages = [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": row["instruction"]},
        {"role": "assistant", "content": row["output"]},
    ]
    return {"text": tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )}


def main(args):
    print(f"Loading {MODEL_NAME} ...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        max_seq_length=MAX_SEQ_LEN,
        dtype=torch.bfloat16,
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    print(model.print_trainable_parameters())

    print("Loading dataset ...")
    train_raw = load_jsonl(args.train)
    val_raw   = load_jsonl(args.val)

    train_ds = Dataset.from_list([format_example(r, tokenizer) for r in train_raw])
    val_ds   = Dataset.from_list([format_example(r, tokenizer) for r in val_raw])
    print(f"  Train: {len(train_ds)}  Val: {len(val_ds)}")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        # ↓ max_seq_length, dataset_text_field, packing all belong on SFTTrainer
        max_seq_length=MAX_SEQ_LEN,
        dataset_text_field="text",
        packing=True,
        args=SFTConfig(
            output_dir=str(args.out),
            per_device_train_batch_size=2,
            gradient_accumulation_steps=8,
            warmup_steps=20,
            num_train_epochs=args.epochs,
            learning_rate=2e-4,
            bf16=True,
            fp16=False,
            logging_steps=10,
            eval_strategy="steps",
            eval_steps=50,
            save_strategy="steps",
            save_steps=100,
            save_total_limit=3,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            optim="adamw_8bit",
            lr_scheduler_type="cosine",
            weight_decay=0.01,
            report_to="none",
        ),
    )

    print("Starting training ...")
    trainer.train()

    adapter_path = Path(args.out) / "lora_adapters"
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    print(f"LoRA adapters saved to {adapter_path}")

    if args.merge:
        merged_path = Path(args.out) / "merged_16bit"
        print(f"Merging to {merged_path} ...")
        model.save_pretrained_merged(
            str(merged_path), tokenizer, save_method="merged_16bit"
        )
        print("Merge complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--train",  type=Path, default=Path("data/train.jsonl"))
    parser.add_argument("--val",    type=Path, default=Path("data/val.jsonl"))
    parser.add_argument("--out",    type=Path, default=Path("checkpoints/acmg-llama3-8b"))
    parser.add_argument("--epochs", type=int,  default=3)
    parser.add_argument("--merge",  action="store_true")
    args = parser.parse_args()
    main(args)