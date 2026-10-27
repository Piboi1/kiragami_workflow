# Running the kirigami pipeline on a Jetson Orin Nano (8 GB)

**What runs where.** The CFD (meshing and solving) runs on SimScale's cloud, never on your machine. The local Python process
only generates a canopy (about 2.5 s, 210 MB RAM), uploads it and polls SimScale (about 300 MB RAM). A Jetson Orin Nano
Super 8 GB is plenty for that, and so is any small computer: the pipeline is not what loads your PC. If your PC struggles, it is
almost certainly the SimScale CAD workbench in the browser (WebGL), which you open for the flow-volume step.

## Install (on the Jetson)
```bash
git clone <your repo url> kiragami_workflow && cd kiragami_workflow/kirigami-optimizer
git checkout claude/kirigami-parachute-pipeline-t24c2i
./jetson/setup_jetson.sh
nano .env          # SIMSCALE_API_KEY=...   (create the key in SimScale > Account settings > API keys)
python main.py check
```
Keys live only in `.env` (git-ignored). Do not paste them into chat, commit them, or copy `.env` into a repo.

## Carry the state over
`state/ledger.json` (every finished design, its Cd and SimScale IDs) and `runs/batch/` are git-ignored, so a fresh clone
starts with nothing. The cloud session commits a copy to `snapshot/` when a batch finishes; restore it with:
```bash
mkdir -p state runs && cp snapshot/ledger.json state/ && cp -r snapshot/batch runs/
python main.py status
```
Without this the optimizer would start from zero and propose designs it has already run.

## Run a batch unattended
```bash
tmux new -s kirigami
python main.py prepare-batch --n 4              # free
# ... do the flow-volume step for each geometry in the SimScale web app (or the automation below) ...
python main.py run-batch --batch <batch_id> --max-core-hours 5 --batch-core-hours 20 --skip-estimate-check   # billed
# detach with Ctrl-b d; re-attach with: tmux attach -t kirigami
```
A crash is safe: the ledger is written after every step and `run-batch` resumes the interrupted design.

## The flow-volume step ("auto clicker")
SimScale's public API cannot make the External flow volume (its OpenAPI spec defines CAD schemas but no endpoints), so it is a UI step.
Options, best first:
1. **Ask SimScale support to enable the CAD-feature API** for your API key. It would remove the clicking altogether.
2. **Browser automation (Playwright), not pixel clicking.** It needs one recorded run; see the steps in the chat reply.
3. Pixel auto-clickers (xdotool/pyautogui) break whenever the window size, zoom or UI layout changes. Avoid.
