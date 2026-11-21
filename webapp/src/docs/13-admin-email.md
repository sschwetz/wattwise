# 13. Admin email

[Contents](index.md) · Previous: [Admin users](12-admin-users.md)

This chapter is for administrators. WattWise sends email for sign-up confirmation, password reset, site invitations, email changes and schedule failures. The **Email** tab of Admin Settings chooses how that email leaves the server.

| Setting | Where email goes |
|---------|------------------|
| No provider active | Through the built-in mail relay, set up with `RELAY_SMARTHOST` when the server was installed. See [Installing with Docker Compose](01-installing-with-docker.md). |
| A provider active | Through that provider's service, such as smtp2go, using the account details you enter here. |

Using a provider needs no change to the server's `.env` file and no restart. WattWise sends the email through a separate container, `mail-actor`, which must be running.

## Opening the Email tab

Click your name in the top right corner, then **Admin Settings**. **Email** is the first tab on the left, so it is the one that opens. **Users** is the second. See [Admin users](12-admin-users.md) for that tab.

At the top, a line shows **Pending**, **Sent (24 h)** and **Failed (24 h)**. Below it, a line says where email goes now: *No provider is active. Email goes through the built-in mail relay.* or *Email goes through* the active provider's name.

If the tab says *No email provider has been declared yet*, the `mail-actor` container is not running or has not started yet. Ask whoever runs the server to start it. See [the fixes below](#when-email-does-not-arrive).

## Setting up smtp2go

You need an [smtp2go](https://www.smtp2go.com/) account, an API key from it that is allowed to send email, and a sender address that your smtp2go account lets you send from. Create the key and the sender in smtp2go. WattWise cannot do that for you.

1. Open the **Email** tab.
2. Under **Provider**, choose **smtp2go**.
3. Click **Change** next to **API key (required)**, paste the key into **New API key (required)**.
4. Type the sender address in **From address (required)**. This is the address your users see as the sender.
5. Click **Save**. **Save** is available only after you change something. The key then shows as **set**. WattWise never shows a saved key again. To replace it, click **Change** and enter a new one. To keep it, leave it alone: saving other fields does not touch it.
6. Click **Activate**, then **Send test email**. See [What Activate does](#what-activate-does).

The form lists whatever settings the chosen provider asks for, so another provider shows different fields.

## What Activate does

**Activate** makes the provider you picked the one WattWise uses, and from then on every email goes to it first. That includes the emails you can see in the app, such as a password reset you request, and the ones you cannot, such as the schedule failure email.

- Only one provider is active. When another is active, WattWise asks you to confirm, because activating turns the other one off.
- **Activate** fails with *Save every required setting before activating* when a required field is empty.
- **Activate** is greyed out, with a note, when the `mail-actor` container no longer lists the provider. The provider list also marks it **(not available)**.
- **Deactivate** asks for confirmation. Afterwards email goes back through the built-in mail relay. When no provider is left active, WattWise also sends the email still in the queue through the relay straight away, up to 200 messages. Messages the mail actor took in the last 2 minutes are left to it. A message the relay refuses is marked failed.

While a provider is active, email waits in a queue until the mail actor sends it. If the actor stops, email waits and nothing falls back to the relay. When the actor starts again, the queued email goes out.

## Sending a test email

**Send test email** queues a message to your own address. It is available only while a provider is active. WattWise says *Test email queued. Check the inbox; delivery depends on the provider.* It does not say *sent*, because WattWise only knows the message is in the queue.

Wait a few seconds, then check your inbox and spam folder. You can send 5 test emails in 10 minutes. After that WattWise says *Too many test emails; try again in a few minutes*.

## Reading the summary line

| Part | Meaning |
|------|---------|
| **Pending** | Messages not yet delivered: waiting in the queue, being sent, or waiting for a retry |
| **Sent (24 h)** | Messages the provider accepted in the last 24 hours |
| **Failed (24 h)** | Messages WattWise gave up on in the last 24 hours |
| **Last error** | The most recent error text any message has, such as `smtp2go send failed: HTTP 401`. It appears only when there is one. |

The numbers refresh every 30 seconds. **Sent** means the provider accepted the message. It does not show that the recipient received it.

When a send fails, WattWise tries again after 30, 60, 120 and 240 seconds. After the fifth failure the message is marked failed and is not tried again. WattWise keeps finished messages for 30 days, without their text, and deletes unsent ones after 7 days.

## When email does not arrive

| Symptom | What to check |
|---------|---------------|
| The tab says *No email provider has been declared yet* | The `mail-actor` container is not running. Ask whoever runs the server to start it. |
| **Pending** grows and nothing is **Sent** | The `mail-actor` container has stopped, or the active provider is one it does not know. Ask whoever runs the server to read its log. To send the queue through the built-in relay meanwhile, **Deactivate** the provider. |
| **Failed** grows and **Last error** ends in `HTTP 401` or `HTTP 403` | The provider refused the API key. Click **Change**, enter a current key and **Save**. The actor picks up the new key within about 30 seconds. |
| **Last error** says `not accepted` | The provider answered but did not accept the message. Check the sender and the account in smtp2go. |
| **Last error** says `settings incomplete` | A required field is empty. Fill it in and **Save**. |
| **Last error** says `HTTP 429` or `HTTP` and a number starting with 5 | The provider is busy or down. WattWise retries, so wait and check again. |
| **Last error** says `claim expired` | The actor took the message five times and never reported a result. Ask whoever runs the server to read its log. |
| The test email shows **Sent** but you cannot find it | Check spam. Check the provider's own activity log. A message the provider accepted is out of WattWise's hands. |
| You got the same email twice | WattWise delivers each message at least once. If the actor could not report a success, the message goes out again after 2 minutes. |
| **Activate** is greyed out and says the mail actor reports the provider as not available | The mail actor no longer lists it. Ask whoever runs the server to check that the `mail-actor` container is up to date. |

If everything on the tab looks healthy and one user still has no email, check that their address is correct on their [user page](12-admin-users.md#details).

## Next

- [Troubleshooting](10-troubleshooting.md#admin-settings-problems)
- [Admin users](12-admin-users.md): the other Admin Settings tab
- [Glossary](11-glossary.md): mail provider and outbox

[Back to the contents](index.md)
