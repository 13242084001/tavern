import logging
import warnings
import os
import io

import pytest
import yaml

from contextlib2 import ExitStack
from box import Box

from .util.general import load_global_config
from .plugins import load_plugins
from .util import exceptions
from .util.dict_util import format_keys
from .util.delay import delay
from .util.loader import IncludeLoader
from .printer import log_pass, log_fail

from .plugins import get_extra_sessions, get_request_type, get_verifiers, get_expected

from .schemas.files import verify_tests, wrapfile


logger = logging.getLogger(__name__)


def run_test(in_file, test_spec, global_cfg):
    """Run a single tavern test

    Note that each tavern test can consist of multiple requests (log in,
    create, update, delete, etc).

    The global configuration is copied and used as an initial configuration for
    this test. Any values which are saved from any tests are saved into this
    test block and can be used for formatting in later stages in the test.

    Args:
        in_file (str): filename containing this test
        test_spec (dict): The specification for this test
        global_cfg (dict): Any global configuration for this test

    Raises:
        TavernException: If any of the tests failed
    """

    # pylint: disable=too-many-locals

    # Initialise test config for this test with the global configuration before
    # starting
    test_block_config = dict(global_cfg)

    if "variables" not in test_block_config:
        test_block_config["variables"] = {}

    tavern_box = Box({
        "env_vars": dict(os.environ),
    })

    test_block_config["variables"]["tavern"] = tavern_box

    if not test_spec:
        logger.warning("Empty test block in %s", in_file)
        return

    if test_spec.get("includes"):
        for included in test_spec["includes"]:
            if "variables" in included:
                formatted_include = format_keys(included["variables"], {"tavern": tavern_box})
                test_block_config["variables"].update(formatted_include)

    test_block_name = test_spec["test_name"]

    # Strict on body by default
    default_strictness = test_block_config["strict"]

    logger.info("Running test : %s", test_block_name)

    with ExitStack() as stack:
        sessions = get_extra_sessions(test_spec, test_block_config)

        for name, session in sessions.items():
            logger.debug("Entering context for %s", name)
            stack.enter_context(session)

        # Run tests in a path in order
        for stage in test_spec["stages"]:
            if stage.get('skip'):
                continue

            test_block_config["strict"] = default_strictness

            # Can be overridden per stage
            # NOTE
            # this is hardcoded to check for the 'response' block. In the far
            # future there might not be a response block, but at the moment it
            # is the hardcoded value for any HTTP request.
            if stage.get("response", {}):
                if stage.get("response").get("strict", None) is not None:
                    stage_strictness = stage.get("response").get("strict", None)
                elif test_spec.get("strict", None) is not None:
                    stage_strictness = test_spec.get("strict", None)
                else:
                    stage_strictness = default_strictness

                logger.debug("Strict key checking for this stage is '%s'", stage_strictness)

                test_block_config["strict"] = stage_strictness
            elif default_strictness:
                logger.debug("Default strictness '%s' ignored for this stage", default_strictness)

            run_stage(sessions, stage, tavern_box, test_block_config)

            if stage.get('only'):
                break


def run_stage(sessions, stage, tavern_box, test_block_config):
    name = stage["name"]

    try:
        r = get_request_type(stage, test_block_config, sessions)
    except exceptions.MissingFormatError:
        log_fail(stage, None, None)
        raise

    tavern_box.update(request_vars=r.request_vars)

    try:
        expected = get_expected(stage, test_block_config, sessions)
    except exceptions.TavernException:
        log_fail(stage, None, None)
        raise

    delay(stage, "before")

    logger.info("Running stage : %s", name)
    try:
        response = r.run()
    except exceptions.TavernException:
        log_fail(stage, None, expected)
        raise

    verifiers = get_verifiers(stage, test_block_config, sessions, expected)
    for v in verifiers:
        try:
            saved = v.verify(response)
        except exceptions.TavernException:
            log_fail(stage, v, expected)
            raise
        else:
            test_block_config["variables"].update(saved)

    log_pass(stage, verifiers)

    tavern_box.pop("request_vars")
    delay(stage, "after")


def run(in_file, tavern_global_cfg={}, pytest_args=None):
    """Run all tests contained in a file

    For each test this makes sure it matches the expected schema, then runs it.
    There currently isn't something like pytest's `-x` flag which exits on first
    failure.

    Todo:
        the tavern_global_cfg argument should ideally be called
        'global_cfg_paths', but it would break the API so we just rename it below

    Note:
        This function DOES NOT read from the pytest config file. This is NOT a
        pytest-reliant part of the code! If you specify global config in
        pytest.ini this will not be used here!

    Args:
        in_file (str): file to run tests on
        tavern_global_cfg (str): file containing Global config for all tests

    Returns:
        bool: Whether ALL tests passed or not
    """

    # will be removed in future
    with ExitStack() as stack:

        if tavern_global_cfg:
            warnings.warn("Passing global_cfg to run() is now deprecated, and will be removed in a future version of Tavern. This will be read from command line arguments or config files automatically in future.", FutureWarning)
            global_filename = stack.enter_context(wrapfile(tavern_global_cfg))

        if pytest_args is None:
            import sys
            sys.argv += ["-k", in_file]
            if tavern_global_cfg:
                sys.argv += ["--tavern-global-cfg", global_filename]
        else:
            pytest_args += ["-k", in_file]
            if tavern_global_cfg:
                pytest_args += ["--tavern-global-cfg", global_filename]

        return pytest.main(args=pytest_args)
