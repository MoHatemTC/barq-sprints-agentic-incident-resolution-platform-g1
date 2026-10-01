"""Calibration dataset for the BARQ G1 Sprint 4 semantic threshold calibration.

This module is the single source of truth for all incident pairs used to
empirically calibrate the similarity threshold (tau) in SemanticCache.

Structure
---------
Each ``IncidentPair`` entry has:

  label     – unique identifier used in result tables
  a / b     – incident signature texts, formatted exactly as
              ``build_incident_signature()`` would produce them
              (Service / Category / Subcategory / Summary / Description)
  expected  – "positive" (should cluster) or "negative" (must never cluster)
  domain    – high-level incident domain used to group results in the report
  rationale – human explanation of why the pair belongs to this class

Adding new pairs
----------------
Simply append new ``IncidentPair`` entries to ``CALIBRATION_PAIRS``.
The calibration script picks them up automatically on the next run.

Pair selection guidelines
--------------------------
POSITIVE pairs  Must:
  * Share the same root cause or symptom class.
  * Use distinctly different phrasing / vocabulary.
  * Ideally come from real concurrent-reporter scenarios.

NEGATIVE pairs  Must:
  * Involve genuinely different systems or failure modes.
  * Include at least one "hard negative" – superficially similar wording
    but a different underlying cause (e.g. two auth incidents on different
    services).
  * Include at least one "maximal negative" – completely unrelated domains
    (IT vs. facilities, IT vs. HR, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IncidentPair:
    label: str
    a: str
    b: str
    expected: str  # "positive" | "negative"
    domain: str  # grouping label (e.g. "network", "identity", "db")
    rationale: str


# ---------------------------------------------------------------------------
# CALIBRATION PAIRS
# ---------------------------------------------------------------------------
# Organised into sections for readability; order does not matter to the script.
# ---------------------------------------------------------------------------

CALIBRATION_PAIRS: list[IncidentPair] = [
    # ======================================================================
    # SECTION A – POSITIVE PAIRS  (must cluster – same root cause)
    # ======================================================================
    # -- Network / VPN -------------------------------------------------------
    IncidentPair(
        label="vpn_auth_password_reset",
        domain="network",
        a=(
            "Service: it infrastructure\n"
            "Category: network\n"
            "Summary: VPN authentication failure after a password reset\n"
            "Description: User cannot connect to corporate VPN. Authentication "
            "fails with 'Credentials rejected' immediately after completing a "
            "password reset. VPN client reports error code 691."
        ),
        b=(
            "Service: it infrastructure\n"
            "Category: network\n"
            "Summary: Cannot log in to VPN following password change\n"
            "Description: Employee attempted to connect to VPN after changing "
            "their domain password and received an authentication error. "
            "Login repeatedly fails. Error 691 shown in VPN client."
        ),
        expected="positive",
        rationale="Identical root cause (VPN auth failure post-password change), different phrasing.",
    ),
    IncidentPair(
        label="vpn_globalprotect_timeout",
        domain="network",
        a=(
            "Service: corporate-vpn\n"
            "Category: network\n"
            "Summary: Unable to connect to GlobalProtect VPN\n"
            "Description: User reports connection timeout after entering credentials on VPN client."
        ),
        b=(
            "Service: corporate-vpn\n"
            "Category: network\n"
            "Summary: VPN GlobalProtect login timeout\n"
            "Description: Cannot authenticate with GlobalProtect corporate VPN gateway, client times out."
        ),
        expected="positive",
        rationale="Same GlobalProtect VPN timeout – concurrent reporters with different wording.",
    ),
    IncidentPair(
        label="wifi_5ghz_drops_concurrent",
        domain="network",
        a=(
            "Category: network\n"
            "Summary: Corporate Wi-Fi drops repeatedly and reconnects every five minutes on 5 GHz\n"
            "Description: Wireless connection drops every 5 minutes on the 5 GHz band. "
            "Affects all devices in building A, floor 3. Users on the 2.4 GHz band are "
            "unaffected. Issue started at 09:00 UTC."
        ),
        b=(
            "Category: network\n"
            "Summary: Wi-Fi keeps dropping every few minutes across the office\n"
            "Description: Multiple users in building A are reporting the corporate Wi-Fi "
            "disconnecting periodically. The disconnection occurs on the 5 GHz network "
            "roughly every 5-6 minutes. Started this morning."
        ),
        expected="positive",
        rationale="Same Wi-Fi instability event reported concurrently, different wording.",
    ),
    IncidentPair(
        label="network_switch_port_flapping",
        domain="network",
        a=(
            "Category: network\n"
            "Summary: Core switch port flapping causing intermittent packet loss on floor 2\n"
            "Description: Network team confirmed port Gi0/24 on core-sw-01 is going up and "
            "down every few seconds. Users on VLAN 20 (floor 2) are seeing 30-40% packet loss."
        ),
        b=(
            "Category: network\n"
            "Summary: Intermittent packet loss and drops on floor 2 network segment\n"
            "Description: Several workstations on the second floor are experiencing network "
            "drops and high latency. Traceroute shows packet loss at core-sw-01. "
            "Ethernet link seems unstable."
        ),
        expected="positive",
        rationale="Same switch port flapping event viewed from two different vantage points.",
    ),
    # -- Identity / Active Directory -----------------------------------------
    IncidentPair(
        label="ad_lockout_stale_credentials",
        domain="identity",
        a=(
            "Category: identity\n"
            "Summary: User Active Directory account locked out due to invalid cached credentials\n"
            "Description: Active Directory account is locked. User cannot log in to "
            "any corporate system. Stale cached credentials on a mobile device are "
            "triggering repeated lockouts."
        ),
        b=(
            "Category: identity\n"
            "Summary: Account locked after repeated failed sign-ins and MFA reset attempts\n"
            "Description: User's AD account is locked after several failed login attempts. "
            "MFA token reset also failed. IT suspects stale credentials on a background "
            "device are causing repeated bind failures."
        ),
        expected="positive",
        rationale="Both are AD lockout incidents with identical root cause (stale cached credentials).",
    ),
    IncidentPair(
        label="mfa_lost_phone",
        domain="identity",
        a=(
            "Category: identity\n"
            "Summary: User lost mobile phone and requires MFA device registration reset\n"
            "Description: The user has lost their phone and can no longer complete MFA. "
            "They need their authenticator app re-enrolled on a new device."
        ),
        b=(
            "Category: identity\n"
            "Summary: Need to re-enroll Authenticator app after phone replacement\n"
            "Description: Employee's phone was stolen. They cannot access their Microsoft "
            "Authenticator app and need IT to reset MFA so they can register a new device."
        ),
        expected="positive",
        rationale="Same MFA re-enrollment scenario after device loss – different surface descriptions.",
    ),
    IncidentPair(
        label="password_expiry_lockout",
        domain="identity",
        a=(
            "Category: identity\n"
            "Summary: User unable to log in – password expired\n"
            "Description: User attempts to log in to their Windows workstation and receives "
            "'Your password has expired and must be changed' message. Cannot access anything."
        ),
        b=(
            "Category: identity\n"
            "Summary: Windows login blocked, forced password reset required\n"
            "Description: Employee is being prompted to change their password on Windows login "
            "and is unable to proceed without doing so. Password change screen loops on submit."
        ),
        expected="positive",
        rationale="Same password-expiry login block – two concurrent reporters.",
    ),
    # -- Email / Outlook -----------------------------------------------------
    IncidentPair(
        label="outlook_disconnected_patch",
        domain="email",
        a=(
            "Category: email\n"
            "Summary: Outlook shows disconnected and no new mail is delivered\n"
            "Description: Outlook desktop client shows 'Disconnected' in the status bar. "
            "No new emails are being received. Issue started after this morning's Windows "
            "update. Reinstalling Outlook did not help."
        ),
        b=(
            "Category: email\n"
            "Summary: Outlook not receiving email, status bar shows disconnected\n"
            "Description: Since the morning patch cycle, Outlook is showing a disconnected "
            "state and incoming mail has stopped. The user has restarted Outlook multiple "
            "times without success."
        ),
        expected="positive",
        rationale="Same Outlook disconnection after patch – two concurrent reporters.",
    ),
    IncidentPair(
        label="exchange_calendar_sync_fail",
        domain="email",
        a=(
            "Service: exchange\n"
            "Category: email\n"
            "Summary: Calendar invitations not appearing in Outlook after accepting\n"
            "Description: Users accept meeting invitations but the events never appear in "
            "their Outlook calendar. Webmail (OWA) shows the same issue. Started 2 hours ago."
        ),
        b=(
            "Service: exchange\n"
            "Category: email\n"
            "Summary: Meeting accepted but calendar entry missing in Outlook\n"
            "Description: Multiple users report that accepted meeting invitations are not "
            "showing up in their calendar. Issue affects both desktop Outlook and OWA. "
            "Events disappear after acceptance."
        ),
        expected="positive",
        rationale=(
            "Same Exchange calendar sync failure – two reporters describing it from different angles."
        ),
    ),
    # -- Printing ------------------------------------------------------------
    IncidentPair(
        label="print_queue_stall_followme",
        domain="printing",
        a=(
            "Category: hardware\n"
            "Summary: Print jobs queue indefinitely on follow-me printer but nothing prints\n"
            "Description: Print jobs submitted to the follow-me printer are stuck in queue. "
            "Nothing prints. The printer is online and has paper and toner. Restarting the "
            "print spooler service did not help."
        ),
        b=(
            "Category: hardware\n"
            "Summary: Follow-me printing not working, jobs stuck in queue\n"
            "Description: Several employees have submitted print jobs that are sitting in "
            "the print queue. The follow-me printer is showing as ready but nothing comes "
            "out. This has been ongoing for 30 minutes."
        ),
        expected="positive",
        rationale="Same follow-me printer queue stall – two concurrent reporters.",
    ),
    IncidentPair(
        label="printer_offline_after_firmware",
        domain="printing",
        a=(
            "Category: hardware\n"
            "Summary: HP LaserJet shows offline after firmware update pushed overnight\n"
            "Description: The HP LaserJet on floor 4 is showing as offline in the print "
            "server after a firmware update was pushed last night. Cycling power did not help."
        ),
        b=(
            "Category: hardware\n"
            "Summary: Floor 4 printer unavailable this morning after overnight maintenance\n"
            "Description: The shared printer on floor 4 cannot accept any print jobs. "
            "Windows shows it as offline. IT pushed a firmware update last night. "
            "Hard reset and reconnection attempts failed."
        ),
        expected="positive",
        rationale=(
            "Same printer offline after firmware update – two observations of the same failure."
        ),
    ),
    # -- Application / Database ----------------------------------------------
    IncidentPair(
        label="db_pool_exhausted_order_service",
        domain="database",
        a=(
            "Service: order-service\n"
            "Category: application\n"
            "Summary: Order service DB connection pool exhausted returning HTTP 500 under load\n"
            "Description: Connection pool is full. All requests to the order service "
            "return HTTP 500. Pool size is 20 and all connections are in use. "
            "DB host: orders-db-prod."
        ),
        b=(
            "Service: order-service\n"
            "Category: application\n"
            "Summary: Order service connection pool exhaustion causing in-flight order loss\n"
            "Description: High traffic event exhausted the database connection pool "
            "for the order service. New requests are being rejected with 500 errors. "
            "In-flight orders may be lost."
        ),
        expected="positive",
        rationale=(
            "Same pool exhaustion event – two reporters describing slightly different impacts."
        ),
    ),
    IncidentPair(
        label="postgres_deadlock_concurrent",
        domain="database",
        a=(
            "Service: database-cluster\n"
            "Category: database\n"
            "Summary: PostgreSQL deadlock detected on orders table during peak load\n"
            "Description: Application logs show 'ERROR: deadlock detected' on the orders "
            "table. Transactions are being rolled back. Peak load is triggering concurrent "
            "row-level locks."
        ),
        b=(
            "Service: database-cluster\n"
            "Category: database\n"
            "Summary: Deadlock errors in PostgreSQL causing transaction rollbacks\n"
            "Description: Multiple services are reporting deadlock errors in Postgres. "
            "Transactions on the orders table are failing and being rolled back. "
            "Issue started during the morning traffic spike."
        ),
        expected="positive",
        rationale="Same PostgreSQL deadlock incident – two different service owners reporting it.",
    ),
    # -- SAP / ERP -----------------------------------------------------------
    IncidentPair(
        label="sap_rfc_timeout_ecc_prod",
        domain="sap",
        a=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP GUI connection times out with RFC_ERROR_COMMUNICATION on ECC production\n"
            "Description: SAP GUI throws RFC_ERROR_COMMUNICATION when opening any "
            "transaction on ECC production. Timeout after ~30 seconds. Other SAP "
            "landscapes (QA, Dev) are unaffected."
        ),
        b=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP ECC production RFC communication error on all transactions\n"
            "Description: Attempting to launch any SAP transaction on the production ECC "
            "system results in an RFC_ERROR_COMMUNICATION timeout. The QA system is fine. "
            "Began approx. 15 minutes ago."
        ),
        expected="positive",
        rationale="Same SAP RFC_ERROR_COMMUNICATION on ECC prod – paraphrased descriptions.",
    ),
    IncidentPair(
        label="sap_gui_session_limit",
        domain="sap",
        a=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP GUI reports maximum session limit reached for user\n"
            "Description: User receives 'Maximum number of sessions reached' error when "
            "trying to open SAP GUI. They have 6 existing sessions they cannot close because "
            "the previous sessions are frozen."
        ),
        b=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: Cannot open new SAP session – session limit exceeded\n"
            "Description: SAP is refusing new logins for the user because the maximum "
            "session count is hit. Old sessions appear frozen and unresponsive. "
            "User needs sessions cleared by Basis team."
        ),
        expected="positive",
        rationale="Same SAP session-limit issue with frozen sessions – two concurrent users reporting.",
    ),
    # -- Hardware / Endpoint -------------------------------------------------
    IncidentPair(
        label="laptop_cpu_throttle_post_update",
        domain="hardware",
        a=(
            "Category: hardware\n"
            "Summary: Laptop high CPU utilization and thermal throttling following system update\n"
            "Description: Laptop fan is running at full speed and the machine is very slow "
            "after yesterday's Windows update. CPU usage stays at 100%."
        ),
        b=(
            "Category: hardware\n"
            "Summary: PC extremely slow and overheating since Windows update last night\n"
            "Description: Since the Windows update applied yesterday evening, the laptop "
            "has been running hot and the CPU is maxed out. Performance is unusable."
        ),
        expected="positive",
        rationale="Same post-update thermal throttling – different language, same root cause.",
    ),
    IncidentPair(
        label="monitor_no_signal_docking",
        domain="hardware",
        a=(
            "Category: hardware\n"
            "Summary: External monitor shows 'no signal' when connected via docking station\n"
            "Description: User's Dell monitor shows a 'No Signal' message when the laptop "
            "is connected through the docking station. Laptop screen works fine. "
            "Other users with the same dock model have the same issue today."
        ),
        b=(
            "Category: hardware\n"
            "Summary: Second screen black and unresponsive through dock\n"
            "Description: The external display is not being detected when docked. "
            "Laptop screen works. Tried a different monitor cable with no change. "
            "Multiple colleagues have reported the same thing this morning."
        ),
        expected="positive",
        rationale="Same docking station / external monitor failure – two concurrent reporters.",
    ),
    # -- Storage / File Access -----------------------------------------------
    IncidentPair(
        label="shared_drive_missing_signin",
        domain="storage",
        a=(
            "Category: storage\n"
            "Summary: Mapped shared drive S: is missing from File Explorer after sign-in\n"
            "Description: The mapped S: drive is not visible in File Explorer after "
            "signing in. Drive was present yesterday. No changes were made to permissions."
        ),
        b=(
            "Category: storage\n"
            "Summary: Network drive S: not appearing on workstation\n"
            "Description: User logs in and the shared S: drive is gone from This PC. "
            "Reconnecting manually fails. Group policy drive mapping appeared to run but "
            "the drive still does not show up."
        ),
        expected="positive",
        rationale="Same mapped drive disappearance – two reporters post-login.",
    ),
    IncidentPair(
        label="onedrive_sync_stuck",
        domain="storage",
        a=(
            "Service: microsoft-365\n"
            "Category: storage\n"
            "Summary: OneDrive sync stuck at 'Processing changes' for over an hour\n"
            "Description: OneDrive for Business has been showing 'Processing changes' "
            "for more than an hour. Files are not syncing. Pausing and resuming sync "
            "does not resolve the issue."
        ),
        b=(
            "Service: microsoft-365\n"
            "Category: storage\n"
            "Summary: OneDrive not syncing – spinner stuck with no progress\n"
            "Description: The OneDrive icon shows a spinning sync indicator that never "
            "completes. Files modified locally are not appearing in SharePoint. "
            "Sign-out and sign-in did not help."
        ),
        expected="positive",
        rationale="Same OneDrive sync stall – two users experiencing the same symptom.",
    ),
    # -- Cloud / Kubernetes --------------------------------------------------
    IncidentPair(
        label="k8s_pod_oom_killed",
        domain="cloud",
        a=(
            "Service: k8s-prod-cluster\n"
            "Category: infrastructure\n"
            "Summary: Kubernetes pods repeatedly OOMKilled in payments namespace\n"
            "Description: Multiple pods in the payments namespace are being OOMKilled. "
            "kubectl describe shows memory limit exceeded. Pod restarts are at 15+ "
            "and the deployment is in a crash loop."
        ),
        b=(
            "Service: k8s-prod-cluster\n"
            "Category: infrastructure\n"
            "Summary: Payment service pods crash-looping due to memory exhaustion\n"
            "Description: Payment service pods keep restarting in Kubernetes. "
            "Logs show OOM kill events. Memory usage spikes to 512Mi limit before "
            "termination. CrashLoopBackOff status observed."
        ),
        expected="positive",
        rationale="Same OOMKilled crash loop in Kubernetes payment pods – two operations engineers reporting.",
    ),
    IncidentPair(
        label="ssl_cert_expiry_service",
        domain="cloud",
        a=(
            "Service: api-gateway\n"
            "Category: security\n"
            "Summary: SSL certificate expired on API gateway causing HTTPS errors\n"
            "Description: The TLS certificate for api.internal.corp expired at 00:00 UTC. "
            "All HTTPS calls to the API gateway are failing with SSL_ERROR_RX_RECORD_TOO_LONG. "
            "Both internal services and external consumers are affected."
        ),
        b=(
            "Service: api-gateway\n"
            "Category: security\n"
            "Summary: API gateway returning TLS handshake failure – cert expired\n"
            "Description: Calls to the production API gateway are failing with certificate "
            "errors. curl shows 'certificate has expired'. Started at midnight. "
            "Several dependent microservices are now down."
        ),
        expected="positive",
        rationale="Same expired certificate on the API gateway – two engineers observing downstream failures.",
    ),
    # ======================================================================
    # SECTION B – NEGATIVE PAIRS  (must NOT cluster – different issues)
    # ======================================================================
    # -- Superficially similar (hard negatives) ------------------------------
    IncidentPair(
        label="vpn_auth_vs_ad_lockout",
        domain="identity",
        a=(
            "Service: it infrastructure\n"
            "Category: network\n"
            "Summary: VPN authentication failure after a password reset\n"
            "Description: User cannot connect to corporate VPN after password reset."
        ),
        b=(
            "Category: identity\n"
            "Summary: User Active Directory account locked out due to invalid cached credentials\n"
            "Description: AD account is locked. Stale cached credentials on a mobile device "
            "are triggering repeated lockouts."
        ),
        expected="negative",
        rationale=(
            "Hard negative: both involve authentication failures but VPN connectivity "
            "issues and AD account lockout have different root causes and resolution paths."
        ),
    ),
    IncidentPair(
        label="outlook_disconnect_vs_exchange_calendar",
        domain="email",
        a=(
            "Category: email\n"
            "Summary: Outlook shows disconnected and no new mail is delivered\n"
            "Description: Outlook client shows 'Disconnected'. No new mail since Windows update."
        ),
        b=(
            "Service: exchange\n"
            "Category: email\n"
            "Summary: Calendar invitations not appearing in Outlook after accepting\n"
            "Description: Accepted meetings do not show in calendar. Webmail also affected."
        ),
        expected="negative",
        rationale=(
            "Hard negative: both are Outlook/Exchange incidents but disconnected mail "
            "delivery is different from calendar sync failure – different server components."
        ),
    ),
    IncidentPair(
        label="db_pool_vs_postgres_deadlock",
        domain="database",
        a=(
            "Service: order-service\n"
            "Category: application\n"
            "Summary: Order service database connection pool exhausted returning HTTP 500"
        ),
        b=(
            "Service: database-cluster\n"
            "Category: database\n"
            "Summary: PostgreSQL deadlock detected on orders table during peak load\n"
            "Description: Transactions are rolling back due to deadlock on orders table."
        ),
        expected="negative",
        rationale=(
            "Hard negative: both are database issues but connection pool exhaustion and "
            "deadlocks are distinct failure modes requiring different remediation."
        ),
    ),
    IncidentPair(
        label="sap_rfc_vs_sap_session_limit",
        domain="sap",
        a=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP GUI connection times out with RFC_ERROR_COMMUNICATION on ECC production"
        ),
        b=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP GUI reports maximum session limit reached for user\n"
            "Description: User receives 'Maximum number of sessions reached' error."
        ),
        expected="negative",
        rationale=(
            "Hard negative: both are SAP GUI issues but RFC communication timeout "
            "and per-user session limit exhaustion are unrelated problems."
        ),
    ),
    IncidentPair(
        label="wifi_drops_vs_switch_port_flapping",
        domain="network",
        a=(
            "Category: network\n"
            "Summary: Corporate Wi-Fi drops repeatedly and reconnects every five minutes on 5 GHz"
        ),
        b=(
            "Category: network\n"
            "Summary: Core switch port flapping causing intermittent packet loss on floor 2\n"
            "Description: Port Gi0/24 on core-sw-01 is flapping. VLAN 20 users see 30-40% packet loss."
        ),
        expected="negative",
        rationale=(
            "Hard negative: both are network incidents but wireless radio instability "
            "and a wired switch port flap have different root causes and remediation."
        ),
    ),
    # -- Cross-domain negatives ----------------------------------------------
    IncidentPair(
        label="vpn_vs_shared_drive",
        domain="cross-domain",
        a=(
            "Service: it infrastructure\n"
            "Category: network\n"
            "Summary: VPN authentication failure after a password reset\n"
            "Description: User cannot connect to corporate VPN after a password reset."
        ),
        b=(
            "Category: storage\n"
            "Summary: Mapped shared drive S: is missing from File Explorer after sign-in\n"
            "Description: The mapped S: drive is not visible in File Explorer after signing in."
        ),
        expected="negative",
        rationale="VPN auth failure vs. missing mapped drive – entirely different systems.",
    ),
    IncidentPair(
        label="ad_lockout_vs_sap_timeout",
        domain="cross-domain",
        a=(
            "Category: identity\n"
            "Summary: User Active Directory account locked out due to invalid cached credentials"
        ),
        b=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP GUI connection times out with RFC_ERROR_COMMUNICATION on ECC production"
        ),
        expected="negative",
        rationale="AD account lockout vs. SAP RFC timeout – unrelated services.",
    ),
    IncidentPair(
        label="db_pool_vs_laptop_cpu",
        domain="cross-domain",
        a=(
            "Service: order-service\n"
            "Category: application\n"
            "Summary: Order service database connection pool exhausted returning HTTP 500 under load"
        ),
        b=(
            "Category: hardware\n"
            "Summary: Laptop high CPU utilization and thermal throttling following system update\n"
            "Description: Laptop fan running at full speed, CPU at 100% after Windows update."
        ),
        expected="negative",
        rationale="Backend DB pool exhaustion vs. individual laptop CPU issue.",
    ),
    IncidentPair(
        label="print_queue_vs_wifi_drops",
        domain="cross-domain",
        a=(
            "Category: hardware\n"
            "Summary: Print jobs queue indefinitely on follow-me printer but nothing prints"
        ),
        b=(
            "Category: network\n"
            "Summary: Corporate Wi-Fi drops repeatedly and reconnects every five minutes on 5 GHz"
        ),
        expected="negative",
        rationale="Printer queue stall vs. Wi-Fi instability – different physical systems.",
    ),
    IncidentPair(
        label="mfa_device_reset_vs_db_pool",
        domain="cross-domain",
        a=(
            "Category: identity\n"
            "Summary: User lost mobile phone and requires MFA device registration reset\n"
            "Description: The user has lost their phone and can no longer complete MFA."
        ),
        b=(
            "Service: order-service\n"
            "Category: application\n"
            "Summary: Order service database connection pool exhausted returning HTTP 500\n"
            "Description: Connection pool exhausted on orders-db-prod. HTTP 500 on all endpoints."
        ),
        expected="negative",
        rationale="User-facing MFA device reset vs. backend connection pool exhaustion.",
    ),
    IncidentPair(
        label="outlook_vs_wifi",
        domain="cross-domain",
        a=("Category: email\nSummary: Outlook shows disconnected and no new mail is delivered"),
        b=(
            "Category: network\n"
            "Summary: Corporate Wi-Fi drops repeatedly and reconnects every five minutes"
        ),
        expected="negative",
        rationale=(
            "Outlook disconnection vs. Wi-Fi instability – distinct symptoms on different "
            "systems; must NOT be auto-clustered even though network could be a common cause."
        ),
    ),
    IncidentPair(
        label="k8s_oom_vs_ssl_cert",
        domain="cross-domain",
        a=(
            "Service: k8s-prod-cluster\n"
            "Category: infrastructure\n"
            "Summary: Kubernetes pods repeatedly OOMKilled in payments namespace"
        ),
        b=(
            "Service: api-gateway\n"
            "Category: security\n"
            "Summary: SSL certificate expired on API gateway causing HTTPS errors\n"
            "Description: TLS cert expired at midnight. All HTTPS calls to API gateway failing."
        ),
        expected="negative",
        rationale="Memory pressure in Kubernetes vs. expired TLS certificate – unrelated failures.",
    ),
    IncidentPair(
        label="laptop_vs_onedrive_sync",
        domain="cross-domain",
        a=(
            "Category: hardware\n"
            "Summary: Laptop high CPU utilization and thermal throttling following system update"
        ),
        b=(
            "Service: microsoft-365\n"
            "Category: storage\n"
            "Summary: OneDrive sync stuck at 'Processing changes' for over an hour\n"
            "Description: OneDrive for Business not syncing for more than an hour."
        ),
        expected="negative",
        rationale="Hardware thermal throttling vs. cloud sync stall – unrelated.",
    ),
    # -- Maximal negatives (IT vs. non-IT) -----------------------------------
    IncidentPair(
        label="elevator_vs_sap",
        domain="facilities",
        a=(
            "Summary: Building B passenger elevator stuck between floors with occupants inside\n"
            "Description: The elevator in building B is stuck. There are people inside. "
            "Emergency services have been notified."
        ),
        b=(
            "Service: sap\n"
            "Category: application\n"
            "Summary: SAP GUI connection times out with RFC_ERROR_COMMUNICATION on ECC production"
        ),
        expected="negative",
        rationale="Physical facilities emergency vs. SAP application timeout – maximally dissimilar.",
    ),
    IncidentPair(
        label="vpn_vs_elevator",
        domain="facilities",
        a=(
            "Service: it infrastructure\n"
            "Category: network\n"
            "Summary: VPN authentication failure after a password reset"
        ),
        b=(
            "Summary: Building B passenger elevator stuck between floors with occupants inside\n"
            "Description: Facilities emergency. Elevator stuck. Occupants trapped."
        ),
        expected="negative",
        rationale="IT network incident vs. physical facilities emergency – lower bound check.",
    ),
    IncidentPair(
        label="hvac_chiller_vs_network",
        domain="facilities",
        a=(
            "Summary: Facilities HVAC water chiller unit leak in basement equipment room 2B\n"
            "Description: Water is leaking from the HVAC chiller unit in basement room 2B. "
            "Facilities team dispatched. Risk of equipment damage."
        ),
        b=(
            "Category: network\n"
            "Summary: Core switch port flapping causing intermittent packet loss on floor 2\n"
            "Description: Port Gi0/24 on core-sw-01 is flapping. VLAN 20 sees packet loss."
        ),
        expected="negative",
        rationale="Physical HVAC leak vs. network switch fault – unrelated domains.",
    ),
    IncidentPair(
        label="payroll_data_vs_db_pool",
        domain="cross-domain",
        a=(
            "Summary: Payroll system displays incorrect salary amount for employee after compensation review\n"
            "Description: Employee's payslip shows wrong base salary after the compensation "
            "review cycle update. The HR team needs to investigate the payroll calculation."
        ),
        b=(
            "Service: order-service\n"
            "Category: application\n"
            "Summary: Order service database connection pool exhausted returning HTTP 500 under load"
        ),
        expected="negative",
        rationale="Payroll data accuracy issue vs. backend connection pool exhaustion – unrelated.",
    ),
]
