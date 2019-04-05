#!/bin/sh -l

shifter --image=ulissigroup/gaspy_regressions:latest \
    --volume=/global/project/projectdirs/m2755/GASpy_workspaces/GASpy:/home/jovyan/GASpy \
    python -c "from gaspy_regress import fit_model0_adsorption_energies; fit_model0_adsorption_energies()"
