# Open House 
This is very much a Work in Progress type of thing, most features I wanted are implemented but the Drop In support there yet as i wanna use this personally first setup everything how i like it and then ill use my setup as a scaffold for the Drop In 

## Install

1. **HACS → ⋮ → Custom repositories**, add this repository with the category
   **Integration**, then install **Open House** from HACS.
2. **Restart Home Assistant.**
3. **Settings → Devices & Services → + Add Integration → Open House**, and walk
   the six setup steps.

Step 3 is the one that is easy to miss, and skipping it is the usual reason there
is no **Open House** tab in the sidebar: HACS only copies the files, and Home
Assistant does not load an integration that no config entry names. Nothing is
wrong until you add it — the code is simply sitting there, unloaded.

The tab is **admin-only**, so a non-admin account will not see it however many
times the page is reloaded.

The release asset is self-contained: it carries the engine, the setup flow's
adapter, the catalog and the built sidebar panel inside the integration, so
nothing else has to be copied anywhere for the panel to load.
