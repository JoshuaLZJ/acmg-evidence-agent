#!/bin/bash
#SBATCH --job-name=acmg_inference_test
#SBATCH --partition=gpu
#SBATCH --gres=gpu:A100:1
#SBATCH --mem=20G
#SBATCH --cpus-per-task=4
#SBATCH --time=0:30:00
#SBATCH --output=logs/inference_test_%j.log
#SBATCH --error=logs/inference_test_%j.err

mkdir -p logs

source /usr/local/anaconda/5.1.0-Python3.6-gcc5/etc/profile.d/conda.sh
conda activate finetune

python - <<'EOF'
import unsloth, torch
from unsloth import FastLanguageModel

ADAPTER_PATH = "checkpoints/acmg-llama3-8b/lora_adapters"

TEST_VARIANTS = [
    {
        "variant":  "BRCA1 c.5266dupC (p.Gln1756ProfsX74)",
        "gene":     "BRCA1",
        "disease":  "Hereditary breast and ovarian cancer",
    },
    {
        "variant":  "TP53 c.817C>T (p.Arg273Cys)",
        "gene":     "TP53",
        "disease":  "Li-Fraumeni syndrome",
    },
    {
        "variant":  "PTEN c.388C>T (p.Arg130Ter)",
        "gene":     "PTEN",
        "disease":  "Cowden syndrome",
    },
]

SYSTEM_PROMPT = (
    "You are a variant interpretation assistant for research prototyping. "
    "Use only retrieved evidence. Separate retrieved facts from inferred conclusions. "
    "Be conservative under uncertainty. All outputs are for research use only "
    "and must not be used for clinical decisions."
)

print(f"Loading model from {ADAPTER_PATH} ...")
model, tokenizer = FastLanguageModel.from_pretrained(
    ADAPTER_PATH,
    max_seq_length=4096,
    dtype=torch.bfloat16,
    load_in_4bit=True,
)
FastLanguageModel.for_inference(model)
print("Model loaded.\n")
print("=" * 80)

for i, v in enumerate(TEST_VARIANTS, 1):
    user_msg = (
        f"Interpret the following variant conservatively for research prototyping.\n\n"
        f"Variant: {v['variant']}\n"
        f"Gene: {v['gene']}\n"
        f"Disease context: {v['disease']}\n\n"
        "Provide a markdown report with sections: Variant Summary, "
        "Retrieved Evidence, Functional Evidence, Literature, "
        "Draft ACMG Assessment, Uncertainties."
    )

    prompt = tokenizer.apply_chat_template(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        tokenize=False,
        add_generation_prompt=True,
    )

    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=1000,
            temperature=0.1,
            do_sample=True,
            repetition_penalty=1.1,
        )

    response = tokenizer.decode(
        outputs[0][inputs.input_ids.shape[1]:],
        skip_special_tokens=True,
    )

    print(f"\n{'=' * 80}")
    print(f"TEST {i}/{len(TEST_VARIANTS)}: {v['variant']}")
    print(f"{'=' * 80}")
    print(response)
    print()

print("=" * 80)
print("Inference test complete.")
EOF