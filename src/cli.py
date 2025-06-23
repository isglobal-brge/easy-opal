import click
from rich.console import Console

from src.commands.setup_cmd import setup
from src.commands.lifecycle_cmds import up, down, restart, reset, status
from src.commands.profile_cmds import profile
from src.commands.config_cmds import config
from src.commands.cert_cmds import cert
from src.commands.update_cmd import update
from src.commands.backup_cmds import backup_group


console = Console()

@click.group()
def main():
    """
    A command-line tool to easily set up and manage an OBiBa Opal environment.
    """
    pass

# Add command groups
main.add_command(setup)
main.add_command(up)
main.add_command(down)
main.add_command(restart)
main.add_command(reset)
main.add_command(status)
main.add_command(profile)
main.add_command(config)
main.add_command(cert)
main.add_command(update)
main.add_command(backup_group)


if __name__ == '__main__':
    main() 