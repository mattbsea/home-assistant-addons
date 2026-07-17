"""TeslaUSB Viewer — browse and watch TeslaUSB dashcam footage from a cloud backend."""

# Single source of truth for the version shown in the UI. Kept in lock-step with the
# add-on version in config.yaml — tests/test_api.py asserts they match so they can't drift.
__version__ = "0.4.2"
