import argparse
import json
import re
import os
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define default sensitive data patterns (can be extended via configuration)
DEFAULT_PATTERNS = {
    "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
    "social_security": r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b",
    "api_key": r"[a-zA-Z0-9_-]{32,45}", # Looser pattern, needs refinement based on context
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
}


def setup_argparse():
    """
    Sets up the argument parser for the command-line interface.

    Returns:
        argparse.ArgumentParser: The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Scans codebase for patterns resembling sensitive data."
    )
    parser.add_argument(
        "path",
        nargs="+",
        help="Path to the file or directory to scan.  Multiple paths allowed.",
    )
    parser.add_argument(
        "-p",
        "--patterns",
        help="Path to a file containing custom regex patterns (JSON format).",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        help="Paths or filenames to exclude from scanning (comma-separated).",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Scan directories recursively.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO).",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Path to output file (defaults to stdout). The file is replaced at the start of each run, so it holds only that run's results.",
    )
    return parser


def load_custom_patterns(pattern_file):
    """
    Loads custom regex patterns from a JSON file.

    Args:
        pattern_file (str): Path to the JSON file containing patterns.

    Returns:
        dict: A dictionary of patterns, or None if an error occurs.
    """
    try:
        with open(pattern_file, "r") as f:
            patterns = json.load(f)
        return patterns
    except FileNotFoundError:
        logging.error(f"Pattern file not found: {pattern_file}")
        return None
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON in pattern file: {pattern_file}")
        return None
    except Exception as e:
        logging.error(f"Error loading patterns: {e}")
        return None


def scan_file(filepath, patterns, excluded_paths=None, output_file=None):
    """
    Scans a single file for sensitive data patterns.

    Args:
        filepath (str): Path to the file to scan.
        patterns (dict): A dictionary of regex patterns.
        excluded_paths (list, optional): List of paths to exclude. Defaults to None.

    Returns:
        bool: False when the file could not be read or a pattern
        failed to compile, True otherwise. Matches that were found
        are still reported before the caller acts on the failure.
    """

    if excluded_paths and any(
        filepath.startswith(path) or filepath.endswith(path) for path in excluded_paths
    ):
        logging.debug(f"Skipping excluded file: {filepath}")
        return True

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        logging.error(f"File not found: {filepath}")
        return False
    except Exception as e:
        logging.error(f"Error reading file {filepath}: {e}")
        return False

    ok = True
    for name, pattern in patterns.items():
        try:
            matches = re.finditer(pattern, content, re.MULTILINE)  # MULTILINE flag
            for match in matches:
                if output_file:
                    try:
                        with open(output_file, "a") as of:
                            of.write(f"File: {filepath}, Pattern: {name}, Match: {match.group(0)}\n")
                    except Exception as e:
                        logging.error(f"Error writing to output file: {e}")
                else:
                     print(f"File: {filepath}, Pattern: {name}, Match: {match.group(0)}")

                logging.warning(
                    f"Potential sensitive data found in {filepath}: Pattern: {name}, Match: {match.group(0)}"
                )
        except re.error as e:
            logging.error(f"Invalid regex pattern {name}: {e}")
            ok = False
    return ok


def scan_directory(dirpath, patterns, recursive=False, excluded_paths=None, output_file=None):
    """
    Scans a directory for sensitive data patterns.

    Args:
        dirpath (str): Path to the directory to scan.
        patterns (dict): A dictionary of regex patterns.
        recursive (bool, optional): Whether to scan recursively. Defaults to False.
        excluded_paths (list, optional): List of paths to exclude. Defaults to None.

    Returns:
        bool: False when any file in the directory could not be
        scanned cleanly, True otherwise.
    """

    if excluded_paths and any(dirpath.startswith(path) for path in excluded_paths):
        logging.debug(f"Skipping excluded directory: {dirpath}")
        return True

    ok = True
    try:
        for root, _, files in os.walk(dirpath):
            if excluded_paths and any(root.startswith(path) for path in excluded_paths):
                logging.debug(f"Skipping excluded directory: {root}")
                continue

            for file in files:
                filepath = os.path.join(root, file)
                if not scan_file(filepath, patterns, excluded_paths, output_file):
                    ok = False

            if not recursive:
                break  # Stop after scanning the top-level directory
    except Exception as e:
        logging.error(f"Error scanning directory {dirpath}: {e}")
        ok = False
    return ok


def main():
    """
    Main function to parse arguments and initiate the scan.
    """
    parser = setup_argparse()
    args = parser.parse_args()

    # Set logging level
    logging.getLogger().setLevel(args.log_level)

    # Load patterns
    patterns = DEFAULT_PATTERNS
    if args.patterns:
        custom_patterns = load_custom_patterns(args.patterns)
        if custom_patterns is None:
            # The caller asked for specific patterns. Silently falling
            # back to the defaults, as before, reports a scan that was
            # never the scan requested.
            sys.exit(1)
        patterns = {**patterns, **custom_patterns}  # Merge custom patterns

    # Handle excluded paths
    excluded_paths = (
        [path.strip() for path in args.exclude.split(",")] if args.exclude else []
    )

    # No ".." check: this program only reads the paths it is given,
    # and a parent relative path is the normal way to point it at a
    # sibling project. The old check rejected those legitimate scans
    # (and any file with ".." in its name) without protecting
    # anything, since nothing here writes or executes by path.
    # The output file is opened once here, in write mode, before
    # scanning. Each match below appends to it, so without this step
    # the file accumulated every earlier run as well and values an
    # operator had already removed from the code stayed in the file
    # reading as current results.
    if args.output:
        try:
            with open(args.output, "w"):
                pass
        except Exception as e:
            logging.error(f"Error writing to output file: {e}")
            sys.exit(1)

    missing = False
    failed = False
    for path in args.path:
        if os.path.isfile(path):
            if not scan_file(path, patterns, excluded_paths, args.output):
                failed = True
        elif os.path.isdir(path):
            if not scan_directory(path, patterns, args.recursive, excluded_paths, args.output):
                failed = True
        else:
            logging.error(f"Path not found: {path}")
            missing = True
    if missing or failed:
        sys.exit(1)


if __name__ == "__main__":
    # Example Usage:
    # 1. Scan a single file: python main.py myfile.txt
    # 2. Scan a directory recursively: python main.py mydirectory -r
    # 3. Use a custom patterns file: python main.py myfile.txt -p patterns.json
    # 4. Exclude specific files or directories: python main.py mydirectory -r -e "excluded_file.txt,excluded_directory"
    # 5. Change log level to debug: python main.py myfile.txt -l DEBUG
    # 6. Write to file: python main.py myfile.txt -o output.txt
    main()