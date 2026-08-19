#!/usr/bin/env bash
#SBATCH --job-name=aces-modules
#SBATCH --cpus-per-task=48
#SBATCH --time=01:00:00
#SBATCH --output=aces-modules-%j.out
#SBATCH --error=aces-modules-%j.err
#SBATCH --nodes=1
set -euo pipefail

cd "${SLURM_SUBMIT_DIR:?Submit this script with sbatch}"

# get_mod.py automatically uses all CPUs requested by --cpus-per-task.
srun --cpu-bind=cores python3 get_mod.py \
    --output "aces_modules-${SLURM_JOB_ID}.json" \
    --progress-every 25
