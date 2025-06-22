import click
from rich.console import Console

from commands.setup_cmd import setup
from commands.lifecycle_cmds import up, down, restart, reset, status
from commands.profile_cmds import profile
from commands.config_cmds import config
from commands.cert_cmds import cert


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


if __name__ == '__main__':
    main() 