"""
Handles reading the program configuration from an INI style file using Python configparser.

Is expecting a file in the following format:

[PLAID]
client_id = xxxxxxxxxxxxxxxxxxxxxxxx
secret = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
public_key = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
environment = development
suppress_warnings=true

[plaid-sync]
dbfile = /data/transactions.db

[Account1]
access_token = access-development-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
account = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

[Account2]
....
"""
import configparser
import time
import shutil


class Config:
    def __init__(self, config_file: str):
        self.config_file = config_file
        self.config = configparser.ConfigParser()
        self.config.read(config_file)

    def get_plaid_client_config(self) -> str:
        return {
            'client_id': self.config['PLAID']['client_id'],
            'secret': self.config['PLAID']['secret'],
            'environment': self.config['PLAID'].get('environment', 'sandbox'),
            'suppress_warnings': self.config['PLAID'].get('suppress_warnings', True),
        }

    @property
    def environment(self):
        return self.config['PLAID']['environment']

    def get_dbfile(self) -> str:
        return self.config['plaid-sync']['dbfile']

    def get_all_config_sections(self) -> str:
        """
        Returns all defined configuration sections, not just accounts
        this is to check if adding a new account would create a duplicate
        section with that name.
        """
        return [
            account
            for account in self.config.sections()
        ]

    def get_enabled_accounts(self) -> str:
        return [
            account
            for account in self.config.sections()
            if (
                account != 'PLAID'
                and account != 'plaid-sync'
                and 'access_token' in self.config[account]
                and not self.config[account].getboolean('disabled', False)
            )
        ]

    def get_account_access_token(self, account_name: str) -> str:
        return self.config[account_name]['access_token']

    def add_account(self, account_name: str, access_token: str):
        """
        Saves an account and its credentials to the configuration file.
        """
        backup_file = f"{self.config_file}.{int(time.time())}.bkp"
        print("Backing up existing configuration to: %s" % backup_file)
        shutil.copyfile(self.config_file, backup_file)

        self.config.add_section(account_name)
        self.config.set(account_name, 'access_token', access_token)

        print("Overwriting existing config file: %s" % self.config_file)
        with open(self.config_file, "w") as f:
            self.config.write(f)
