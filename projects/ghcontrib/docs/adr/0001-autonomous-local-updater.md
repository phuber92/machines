# Run the contribution updater autonomously on the Pico

The Pico 2 W fetches contribution snapshots directly from GitHub and supplies them to a future GPIO LED graph on the same device. The device owns contribution retrieval, local storage, network recovery, and certificate trust. This keeps the display self-contained within the device's resource limits.
