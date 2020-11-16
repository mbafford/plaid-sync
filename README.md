# Overview

`plaid-sync` is a Python based command-line interface to the [Plaid API](https://plaid.com/docs/api/) that synchronizes your bank/credit card transactions to a local SQLite database.

## Use-Cases

The data can be easily queried using SQLite JSON query syntax, so this could be used as part of a standard personal finance workflow. Determine how often you eat out, how
much you spend on groceries, when was the last time you shopped at a particular store.

My particular use-case is to process the resulting trasansactions into a 
[beancount](http://furius.ca/beancount/) journal file for use with [Fava](https://beancount.github.io/fava/) as well as the beancount Python API for custom reporting/charting.

## Plaid

Plaid's API provides a way to connect to your bank accounts to obtain transactions
and balance data from a large variety of banks in a standardized way. Plaid offers a "free forever" developer program with 100 accounts supported, and a pay as you go "Launch" program with no publicly stated pricing, but once you're signed up you have access to the pricing details. API keys (beyond the fake data sandbox mode) are gated behind a customer service agent approving your use-case.

For more details, see [Plaid's website](https://dashboard.plaid.com/signup).

## Plaid Link

Plaid no longer supports an API-based method for establishing new accounts or updating
existing accounts with credentials (username, password). This helps prevent third party
programs from getting access to user's credentials, but also makes programs like this
have to go through some more hoops to set up. 

Specifically, you have to use the [Plaid Link API](https://plaid.com/docs/link/), and run that in a web browser. That the uses iframes to isolate the credentials updates to
just Plaid's servers/code. 

This program incorporates a very simple webserver to handle the Plaid Link portion. This is only needed when setting up a new account, or when an existing account needs a credentials refresh. In my experience, the credentials refresh is going to happen periodically - one bank has a 90 day policy, another needed it after I was locked out of my account due to invalid login attempts. 

## Limitations

Plaid's lower tiers do not provide investment or libabilities details, so this cannot
sync mortgage or brokerage transactions. It does work fine with all of the credit card and bank accounts I need it to.

# Usage

## Installation

Developed for Python 3.7.4.

There is only one mandatory external dependency, the Plaid API (`plaid-python==7.1.0`).

There is one optional depenency, [`tqdm`](https://github.com/tqdm/tqdm), if you want fancy progress bars during syncing. If you don't install it, you just get unfancy print
messages. My current account load takes about 4 seconds to sync.

This is not set up to be run/installed as a command line program, but could be easily done so.

My standard approach is to clone the repository, set up a virtual environment, and install the necessary dependencies in that environment.

## Configuration

Establish a configuration file with your Plaid credentials. This is in standard INI format. There is an example file `config/sandbox`;

```
[PLAID]
client_id = XXXXXXXXXXXXX
secret = XXXXXXXXXXXXXX
public_key = xXXXXXXXXXXXXXXX
environment = sandbox

[plaid-sync]
dbfile = /tmp/sandbox.db

; account definitions will be added by plaid-sync
; when --link-account step is run
;
; but if you already have Plaid access tokens, you
; can add them as such:
;
; [Friendly Account Name]
; access_token = XXXXXXXXXXXX
; disabled=false/true
```

Once you've set up the basic credentials, run through linking a new account:

```$ ./plaid-sync.py -c config/sandbox --link 'Test Chase'
Open the following page in your browser to continue:
    http://127.0.0.1:4583/link.html
```

Open the above link, follow the instructions (click the button, find your bank, enter credentials).

The console will then update with confirmation:

```Public token obtained [public-sandbox-XXXX]. Exchanging for access token.
Access token received: access-sandbox-XXXX

Saving new link to configuration file
Backing up existing configuration to: config/sandbox.1605537592.bkp
Overwriting existing config file: config/sandbox

Test Chase is linked and is ready to sync.
```

Your config file will have updated to have a line for this new account:

```
[Test Chase]
access_token = access-sandbox-XXXX
```

And you can now run the sync process:

```
$ ./plaid-sync.py -c config/sandbox
                                                                                       
Finished syncing 2 Plaid accounts

Test Chase : 16 new transactions (0 pending),  0 archived transactions over 5 accounts
```

## Updating an Expired Account

Occasionally you'll get an error like this while syncing:

```./plaid-sync.py -c config/sandbox                       

Finished syncing 2 Plaid accounts

Test Chase :  0 new transactions (0 pending),  0 archived transactions over 0 accounts
           : *** Plaid Error ***
           : ITEM_LOGIN_REQUIRED: the login details
           : of this item have changed (credentials,
           : MFA, or required user action) and a user
           : login is required to update this
           : information. use Link's update mode to
           : restore the item to a good state
           : *** re-run with: ***
           : --update 'Test Chase'
           : to fix
```

This just means your bank either isn't accepting the old credentials, or has a setup/arrangement where the login needs to be refreshed periodically. 

This process requires the Plaid Link (web browser) process again, but it's fairly painless. 

Just run the update process:

```
$ ./plaid-sync.py -c config/sandbox --update 'Test Chase'
Starting account update process for [Test Chase]

Open the following page in your browser to continue:
    http://127.0.0.1:4583/link.html
```

Open the page in your browser, click the button, enter new credentials, return to the console, confirm the process completed:

```
Public token obtained [public-sandbox-be30eb9a-8bcb-4dd0-9cf5-048ca7dfa5a3].

There is nothing else to do, the account should sync properly now with the existing credentials.
```

The sync process should run normally again.

# WARNINGS

When linking/setting up a new account, your public token (temporary) and access token (permanent) cannot be recovered if lost. I've taken care to show them to you during this process in both the browser and the command line so you can recover the flow if 
something goes wrong. Once you've saved the access token, you don't need the public token anymore.

This is important for accounts in the "test" level, as there is a 100 lifetime account limit.

# TODO

* Request and store balance amounts in a new table in the SQLite database.
* Formalize setup process, add `setup.py`, ensure support for installation through [pipx](https://github.com/pipxproject/pipx).