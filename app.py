#!/usr/bin/env python3

# Standard library imports
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Third-party imports
import altair as alt
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from snowflake.snowpark.session import Session

# Local/application imports
import handler_tasks.blocks as blocks
from handler_tasks.cortalyst import Cortlayst
from handler_tasks.db_setup import DBSetup
from log.logger import get_logger as logger

logger = logger("demo_mate_bot")

try:
    session = Session.builder.config(
        "connection_name", os.getenv("SNOWFLAKE_CONNECTION_NAME", "trial-key")
    ).create()
    logger.debug(
        f"Account:{session.conf.get('account')},User:{session.conf.get('user')}"
    )
    session.sql("DESC USER")
except Exception as e:
    logger.error(f"Error establishing connection,{e}", exc_info=True)
    sys.exit(1)

# Initializes your app with your bot token and socket mode handler
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

db_setup: DBSetup = DBSetup(session=session)

if os.path.exists(".dbinfo"):
    logger.debug("Loading db and schema info from file .dbinfo")
    with open(".dbinfo", "r") as file:
        db_info = json.load(file)
        db_setup.db_name = db_info["db_name"]
        db_setup.schema_name = db_info["schema_name"]
        logger.debug(
            f"App will use DB: '{db_setup.db_name}' and Schema: '{db_setup.schema_name}'"
        )


def do_setup(
    client,
    channel_id,
    logger,
    db_name: str = "demo_db",
    schema_name: str = "data",
):
    """
    Set up database and schema configurations for a client channel.

    Args:
        client: Database client object used for establishing connection
        channel_id: Unique identifier for the channel being configured
        logger: Logger instance for tracking setup process and errors
        db_name (str, optional): Name of the database to be used. Defaults to "demo_db"
        schema_name (str, optional): Name of the schema to be created/used. Defaults to "data"

    Returns:
        None

    Raises:
        Exception: If anything goes wrong with objection creation
    """

    logger.info("Running Database Setup")

    try:
        client.chat_postMessage(
            channel=channel_id,
            text=f"Wait for few seconds for the setup to be done :hourglass_flowing_sand:",
        )

        global db_setup
        db_setup.db_name = db_name
        db_setup.schema_name = schema_name
        ## call the db setup
        db_setup.do(
            client,
            channel_id=channel_id,
        )

        # Send a message with the input value
        client.chat_postMessage(
            channel=channel_id,
            text=f"""
*Congratulations!!* Demo setup successful :tada:.

Try this query in *Snowsight* to view the loaded data:  
```
SELECT * FROM {db_name}.{schema_name}.SUPPORT_TICKETS;
```""",
        )
        ## write to file for persistence
        with open(".dbinfo", "w") as file:
            json.dump(
                {"db_name": db_name, "schema_name": schema_name},
                file,
                indent=2,
            )
    except Exception as e:
        logger.error(f"Error handling db setup: {e}")
        files = list(Path("src/templates").glob("*.yaml"))
        for file_path in files:
            file_path.unlink()
            logger.info(f"Removed: {file_path}")
        client.chat_postMessage(
            channel=channel_id,
            text=f"Sorry, error setting up database.{e}",
        )


@app.command("/setup")
def setup_handler(ack, client, command, respond):
    """
    Handle setup command events from Slack, process the command, and send responses.

    Args:
        ack: Function to acknowledge receipt of the command to Slack
        client: Slack client instance used to interact with the Slack API
        command: Dictionary containing command data including:
            - text: The text of the command
            - user_id: ID of the user who triggered the command
            - channel_id: ID of the channel where command was issued
            - team_id: ID of the Slack workspace
        respond: Function to send delayed responses to the command

    Returns:
        None
    """
    try:
        ack()
        command_text = command.get("text", "").strip()
        logger.debug(f"command_text:{command_text}")
        if not command_text:
            logger.debug("Sending Block")
            try:
                # Send the response with input block
                respond(
                    blocks=blocks.db_schema_setup,
                    response_type="ephemeral",  # Only visible to the user who triggered the command
                )
            except Exception as e:
                logger.error(f"Failed to send response: {e}")
                # Fallback response
                respond(
                    text="Sorry, there was an error displaying the setup form.",
                    response_type="ephemeral",
                )
        else:
            try:
                logger.debug(f"Body Text:{command_text}")
                db_name, schema_name = tuple(command_text.strip().split())
                channel = command["channel_id"]
                do_setup(
                    channel_id=channel,
                    client=client,
                    db_name=db_name,
                    schema_name=schema_name,
                    logger=logger,
                )
            except ValueError as e:
                respond(
                    text="Invalid format. Please provide both database name and schema name.",
                    response_type="ephemeral",
                )
            except Exception as e:
                logger.error(f"Setup error: {e}")
                respond(text=f"Error during setup: {str(e)}", response_type="ephemeral")
    except Exception as e:
        logger.error(f"Global handler error: {e}")
        try:
            respond(text="An unexpected error occurred.", response_type="ephemeral")
        except:
            # If respond fails, try using client as fallback
            client.chat_postEphemeral(
                channel=command["channel_id"],
                user=command["user_id"],
                text="An unexpected error occurred.",
            )


@app.action("setup_db")
def action_setup_db(ack, body, client, logger):
    """
    Handle Slack interactive component actions related to database setup.

    Args:
        ack: Function to acknowledge receipt of the action to Slack
        body: Dictionary containing the action payload including:
            - trigger_id: ID of the triggered action
            - user: Information about the user who triggered the action
            - actions: Array of action objects containing values/selections
            - response_url: URL for sending delayed responses
        client: Slack client instance for making API calls
        logger: Logger instance to track the setup process and any errors

    Returns:
        None
    """
    logger.debug(f"Received Message Event: {body}")

    # Acknowledge the button click
    ack()

    # Get the input value
    db_name = body["state"]["values"]["db_name_input_block"]["db_name"]["value"]
    schema_name = body["state"]["values"]["schema_name_input_block"]["schema_name"][
        "value"
    ]
    channel = body["channel"]["id"]
    do_setup(
        channel_id=channel,
        client=client,
        db_name=db_name,
        schema_name=schema_name,
        logger=logger,
    )


@app.command("/cleanup")
def cleanup_handler(ack, client, command, respond):
    """
    Handle cleanup of demo resources typically a Database.

    Args:
        ack: Function to acknowledge receipt of the command to Slack
        client: Slack client instance used to interact with the Slack API
        command: Dictionary containing command data including:
            - text: The text of the command
            - user_id: ID of the user who triggered the command
            - channel_id: ID of the channel where command was issued
            - team_id: ID of the Slack workspace
        respond: Function to send delayed responses to the command

    Returns:
        None
    """
    logger.debug(f"Received Demo Cleanup Command: {command}")
    ack()
    global session
    channel_id = command["channel_id"]
    # Send the response with wait message
    client.chat_postMessage(
        channel=channel_id,
        text=f"Wait for few seconds for the cleanup to be done :hourglass_flowing_sand:",
    )
    db_name = command.get("text", "").strip()
    logger.debug(f"command_text:{db_name}")
    try:
        if db_name is not None:
            logger.debug(f"Dropping :command_text:{db_name}")
            _count = session.sql(f"DROP DATABASE {db_name}").count()
            if _count > 0:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f":white_check_mark: Database `{db_name}` dropped successfully.",
                )

    except Exception as e:
        logger.error(f"Failed to cleanup: {e}")
        # Fallback response
        respond(
            text="Sorry, there was an error during cleanup of Database {db_name}.",
            response_type="ephemeral",
        )


@app.command("/cortalyst")
def handle_cortalyst(ack, client: WebClient, say, command, respond, logger):
    """
    Handle Cortex Analyst(cortalyst) Slack commands and process requests.

    Args:
        ack: Function to acknowledge receipt of the command to Slack
        client (WebClient): Slack WebClient instance for making API calls
        say: Function to send messages to the conversation
        command: Dictionary containing command data including:
            - text: The text/arguments provided with the command
            - user_id: ID of the user who triggered the command
            - channel_id: Channel where command was issued
            - team_id: ID of the Slack workspace
        respond: Function to send delayed responses to the command
        logger: Logger instance for tracking command execution and errors

    Returns:
        None

    Raises:
        Exception: Any error that occurs during command processing,
            including Slack API errors, invalid commands, or processing failures
    """
    ack()
    logger.debug(f"Received Command 'cortalyst': {command}")
    try:
        command_text = command.get("text", "").strip()
        if not command_text:
            try:
                logger.debug(f"No question asking user")
                # Send the response with input block
                respond(
                    blocks=blocks.cortex_question,
                    response_type="ephemeral",  # Only visible to the user who triggered the command
                )
            except Exception as e:
                logger.error(f"Failed to send response: {e}")
                # Fallback response
                respond(
                    text="Sorry, there was an error displaying the question form.",
                    response_type="ephemeral",
                )
        else:
            logger.debug(f"Question:{command_text}")
            try:
                channel_id = command["channel_id"]
                ask_cortex_analyst(
                    channel_id=channel_id,
                    client=client,
                    say=say,
                    logger=logger,
                    question=command_text,
                )
            except Exception as e:
                logger.error(f"Cortalyst error: {e}")
                respond(
                    text=f"Error asking Cortex Analyst: {str(e)}",
                    response_type="ephemeral",
                )
    except Exception as e:
        logger.error(f"Cortalyst::Failed to send response: {e}")
        try:
            respond(text="An unexpected error occurred.", response_type="ephemeral")
        except:
            # If respond fails, try using client as fallback
            client.chat_postEphemeral(
                channel=command["channel_id"],
                user=command["user_id"],
                text="An unexpected error occurred.",
            )


@app.action("ask_cortex_analyst")
def action_ask_cortex_analyst(ack, body, client, respond, say, logger):
    """
    Handle interactive actions related to Cortex Analyst inquiries or requests.

    Args:
        ack: Function to acknowledge receipt of the action to Slack
        body: Dictionary containing the action payload including:
            - trigger_id: ID of the triggered action
            - user: Information about the user who triggered the action
                - id: User's Slack ID
                - username: User's display name
            - actions: Array of action objects containing user inputs/selections
            - response_url: URL for sending delayed responses
        client: Slack client instance for making API calls
        respond: Function to send delayed responses to the original message
        say: Function to send new messages to the conversation
        logger: Logger instance for tracking action processing and errors

    Returns:
        None

    Raises:
        Exception: Any error during action processing, including:
            - Slack API errors
            - Invalid user inputs
            - Cortex system connection issues
            - Response formatting errors
    """
    ack()
    try:
        logger.debug(f"Received Message Event: {body}")
        global db_setup  # make sure we use the global one

        question = body["state"]["values"]["analyst_question_block"]["question"][
            "value"
        ]
        channel_id = body["channel"]["id"]
        ask_cortex_analyst(channel_id, client, say, logger, question)

    except Exception as e:
        logger.error(f"Failed to send request to Cortex Analyst: {e}")
        # Fallback response
        respond(
            text="Sorry, there was an error askingCortex Analyst .",
            response_type="ephemeral",
        )


def ask_cortex_analyst(channel_id: str, client: WebClient, say, logger, question: str):
    """
    Send a question to the Cortex Analyst system and handle the response in a Slack channel.

    Args:
        channel_id (str): The ID of the Slack channel where the response should be posted
        client (WebClient): Slack WebClient instance for making API calls
        say: Function to send messages to the conversation
        logger: Logger instance for tracking the question processing and responses
        question (str): The actual question or request to be processed by Cortex Analyst

    Returns:
        None

    Raises:
        Exception: Any error during question processing, including:
            - Connection failures to Cortex system
            - Invalid question format
            - Slack messaging errors
            - Response processing failures

    """
    try:
        sanitized_question = " ".join(question.splitlines())

        logger.debug(f"Question:{sanitized_question}")
        logger.debug(f"Using DB:{db_setup.db_name},Schema:{db_setup.schema_name}")

        client.chat_postMessage(
            channel=channel_id,
            text=f":hourglass_flowing_sand: Wait for a few seconds... while I ask the Cortex Analyst :robot_face:",
        )

        if os.getenv("PRIVATE_KEY_FILE_PATH") is None:
            raise Exception(
                f"Require PRIVATE_KEY_FILE_PATH to be set. Consult Snowflake documentation https://docs.snowflake.com/user-guide/key-pair-auth#configuring-key-pair-authentication."
            )

        cortalyst = Cortlayst(
            account=session.conf.get("account"),
            user=session.conf.get("user"),
            host=session.conf.get("host"),
            private_key_file_path=os.getenv("PRIVATE_KEY_FILE_PATH"),
        )

        ans = cortalyst.answer(question)

        content = ans["message"]["content"]
        show_response(
            client,
            channel_id,
            content,
            say,
        )
    except Exception as e:
        raise Exception(e)


def show_response(client: WebClient, channel_id, content: List[Dict[str, Any]], say):
    """
    Display Cortex Analyst's JSON response as formatted messages in a Slack channel.

    Args:
        client (WebClient): Slack WebClient instance for making API calls
        channel_id: ID of the Slack channel where the analysis should be displayed
        content (List[Dict[str, Any]]): JSON response from Cortex Analyst containing:
            - analysis_results: Detailed findings and insights
            - recommendations: Suggested actions or next steps
            - confidence_score: Reliability score of the analysis
            - sql: Generated SQL queries
        say: Function to send messages to the conversation

    Returns:
        None

    Raises:
        Exception: Any error during response formatting/display, including:
            - Malformed JSON response
            - Missing required response fields
            - Slack message formatting errors
            - Channel posting permissions issues
    """
    try:
        for item in content:
            match item["type"]:
                case "sql":
                    # Send raw generated query for reference
                    logger.debug(f"Generating text block with generated SQL")
                    query = item["statement"]
                    say(
                        blocks=blocks.create_sql_block(query),
                        text="Generated SQL",
                    )

                    # Build and Display Dataframe for Query Results
                    logger.debug(f"Building query result")
                    df = session.sql(query).to_pandas()
                    say(
                        blocks=blocks.create_df_block(df),
                        text="Query Result",
                    )

                    # Visualization
                    # only I have enough columns for building a graph
                    if len(df.columns) > 1:
                        chart = (
                            alt.Chart(df)
                            .mark_arc()
                            .encode(theta="TICKET_COUNT", color="SERVICE_TYPE")
                        )

                        # Save chart to bytes buffer as PNG
                        buffer = io.BytesIO()
                        chart.save(buffer, format="png")
                        buffer.seek(0)
                        image_bytes = buffer.getvalue()

                        # Upload image bytes to Slack
                        uploaded_file = client.files_upload_v2(
                            channel=channel_id,
                            file=image_bytes,
                            filename="chart.png",
                            initial_comment="Generating chart...",
                        )

                        logger.info(f"Uploaded File:{uploaded_file}")

                        # say(
                        #     blocks=blocks.visualization_block(uploaded_file),
                        #     text="Query Result",
                        # )
                case _:
                    pass
    except Exception as e:
        logger.error(f"Error sending response {e}", exc_info=True)
        raise Exception(f"Error sending response {e}")


# Error handler
@app.error
def error_handler(error, body, logger):
    """
    Handle and log errors that occur during Slack app operations.

    Args:
        error: Exception object containing error details and traceback
        body: Dictionary containing the context of the failed operation including:
            - type: Type of the Slack event/action that failed
            - user: Information about the user who triggered the action
            - channel: Channel where the error occurred
            - ts: Timestamp of the failed operation
        logger: Logger instance for recording error details and context

    Returns:
        None
    """
    logger.error(f"Error: {error}")
    logger.error(f"Request body: {body}")


def main():
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


# Start your app
if __name__ == "__main__":
    main()
