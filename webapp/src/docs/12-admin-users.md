# 12. Admin users

[Contents](index.md) · Previous: [Glossary](11-glossary.md)

This chapter is for administrators. An administrator can open **Admin Settings** to find any user, change their name and email, give them access to sites, send a password reset email, reset their two-factor sign-in and decide who else is an administrator. Other users never see these pages.

The account WattWise creates on first start is an administrator. On a server that already had users when it first gained the Administrator role, the user with the lowest id became the administrator.

> **Warning:** An administrator who can change an email address, send a password reset and reset two-factor sign-in can take over any account. Give the Administrator role only to people you trust with every account. WattWise logs which action an administrator took on which user, but not the values they entered.

## Opening Admin Settings

Click your name in the top right corner, then **Admin Settings**. The item appears only for administrators. If you do not see it, read [the Admin Settings fixes](10-troubleshooting.md#admin-settings-problems).

The page has a **Users** tab on the left.

## Finding a user

The **Users** tab lists every user with three columns:

| Column | Shows |
|--------|-------|
| **User** | Name and email. An **Admin** badge marks administrators. Click the name to open the user. |
| **Manages** | Sites the user owns or manages |
| **Views** | Sites the user can only view |

Type in **Search users** to filter by name or email. The list is not paginated.

## A user's page

Click a user to open their page. The arrow at the top left returns to the list. The page has four sections.

### Details

Change the **Display name** or **Email** and click **Save**. **Save** is available only after you change something. Clear the name to remove it. **Remove photo** deletes the user's photo and appears only when they have one.

Changing an email takes effect at once, without a confirmation email. It does not change whether the address counts as confirmed, and it cancels any email change or password reset the user had pending. People already signed in stay signed in. If another account already uses the address, WattWise shows *An account with this email already exists* and saves nothing.

### Site access

The section lists the user's sites with their role.

- The site's **Owner** is shown as text. Ownership cannot be changed here.
- For other sites, choose **Manager** or **Viewer** in the role list. The change saves when you pick it. A [manager](04-sites.md#sharing-a-site) has the owner's rights on the site, except over other managers and who owns it. A viewer reads only.
- **Remove** asks for confirmation, then ends the user's access. Any invitation they had accepted for that site is marked revoked.
- To add the user to another site, pick it under **Add to site**, pick a role and click **Add**. Only sites that the user does not already have are listed.

### Security

| Control | What it does |
|---------|--------------|
| **Send password reset email** | Asks WattWise to email the user a password reset link, the same email a user gets from the [forgotten password](03-signing-in.md#forgotten-password) page. It does not set a password or show you one. The button then reads **Reset email requested** and says delivery is not confirmed, because WattWise reports success even when the email could not be sent. The link lasts 1 hour. |
| **Reset two-factor sign-in** | After you confirm, removes the user's authenticator and all their recovery codes. At the next sign-in they set up two-factor sign-in again. Use it when someone has lost their phone and has no recovery codes. |
| **Amber API key** | Shows whether a key is set, never the key. **Change** saves a new key. **Clear** removes it, after which price data stops loading for the sites the user owns until a new key is set. Sites they only manage or view use their owner's key. |

#### Reset two-factor sign-in

Check that the request really comes from the user before you reset. Until they set it up again, their account is protected by the password alone.

### Administrator

The section says whether the user is an administrator. **Make administrator** grants the role at once. **Remove administrator** asks for confirmation and takes away the user's access to Admin Settings.

WattWise keeps at least one administrator. When the user is the only one, **Remove administrator** is unavailable and a note says so. To step down, make someone else an administrator first.

## Next

- [Troubleshooting Admin Settings](10-troubleshooting.md#admin-settings-problems)
- [Sharing a site](04-sites.md#sharing-a-site): roles on a site, as owners and managers see them
- [Account settings](09-account-settings.md): your own password, email and authenticator

[Back to the contents](index.md)
