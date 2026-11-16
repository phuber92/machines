# Provision certificate trust manually

Install the required trusted CA certificates on the Pico and update them manually when needed. Long-lived trust certificates make this maintenance acceptable, keeping the application on stock MicroPython without automatic trust distribution or signing infrastructure. Continue verifying GitHub's certificate and hostname; unattended recovery is limited to connections that validate against the installed trust certificates.
