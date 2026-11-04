"""
app/content/python_curriculum.py — the plan of Bookey's own Python book.

Lessons are written once into python_book.json (scripts/build_python_book.py). Each
learner's copy is cut from it by app/books/personal.py using their survey answers:
`tier` says from which starting level a chapter is needed, `track` marks a chapter
that is only included for that goal.
"""
from __future__ import annotations

# (number, title, summary, tier, track, lessons)
#   tier: "core" every learner · "new" absolute beginners only · "basics" beginners + basics
CHAPTERS: list[tuple[int, str, str, str, str | None, list[str]]] = [
    (1, "Meet Python", "What programming is, and your first working programs.", "new", None, [
        "What a program really is", "Your first program with print", "Comments and clean code",
        "Reading your first error message"]),
    (2, "Variables and Data Types", "Name values and learn the kinds of data Python holds.", "new", None, [
        "Variables: naming a value", "Numbers: int and float", "Strings: working with text",
        "Booleans and None", "Converting between types"]),
    (3, "Operators and Expressions", "Calculate, compare and combine values.", "new", None, [
        "Arithmetic operators", "Comparison operators", "Logical operators: and, or, not",
        "Operator order and assignment shortcuts"]),
    (4, "Strings in Depth", "Slice, clean and format text like a professional.", "basics", None, [
        "Indexing and slicing", "Useful string methods", "f-strings for clean output",
        "Splitting and joining text"]),
    (5, "Making Decisions", "Let your program choose a path.", "basics", None, [
        "if and else", "elif for many choices", "Truthy and falsy values", "Nested conditions and match"]),
    (6, "Loops", "Repeat work safely with for and while.", "basics", None, [
        "for loops and range", "while loops that always stop", "break, continue and else",
        "Common loop patterns"]),
    (7, "Lists and Tuples", "Keep ordered collections of values.", "basics", None, [
        "Creating and reading lists", "Changing lists", "Sorting and searching", "Tuples and unpacking"]),
    (8, "Dictionaries and Sets", "Look things up by key and keep unique items.", "basics", None, [
        "Dictionary basics", "Looping over dictionaries", "Nested data", "Sets for unique values"]),
    (9, "Functions", "Package logic into reusable, testable pieces.", "core", None, [
        "Defining and calling functions", "Parameters and default values", "Return values",
        "Scope: where names live", "*args, **kwargs and lambda", "Recursion"]),
    (10, "Files and Errors", "Save data and handle things going wrong.", "core", None, [
        "Reading and writing text files", "try and except", "Raising your own errors",
        "Working with JSON", "Working with CSV"]),
    (11, "Modules and the Standard Library", "Reuse code that already exists.", "core", None, [
        "Importing modules", "Writing your own module", "math, random and statistics",
        "Dates and times with datetime", "Paths with pathlib", "pip and virtual environments"]),
    (12, "Object-Oriented Python", "Model real things with classes.", "core", None, [
        "Classes and objects", "Methods and __init__", "Inheritance",
        "Special methods like __str__", "Dataclasses"]),
    (13, "Pythonic Code", "Write Python the way experienced developers do.", "core", None, [
        "List and dict comprehensions", "Iterators and generators", "Decorators", "Type hints",
        "Context managers"]),
    (14, "Debugging and Testing", "Find bugs quickly and prove your code works.", "core", None, [
        "A method for debugging", "Logging instead of print", "Writing tests with assert",
        "unittest in practice"]),
    (15, "Automation with Python", "Make the computer do your boring work.", "core", "automation", [
        "Organising files automatically", "Cleaning text with regular expressions",
        "Building a command-line tool"]),
    (16, "Working with Data", "Turn raw numbers into answers.", "core", "data", [
        "Cleaning a dataset with plain Python", "Grouping and summarising data",
        "Finding insights and reporting them"]),
    (17, "Python for the Web", "Understand how Python talks to the web.", "core", "web", [
        "How HTTP requests work", "Reading API data as JSON", "Designing a small web API"]),
    (18, "First Steps in AI", "See how machines learn from data, in plain Python.", "core", "ai", [
        "What machine learning really is", "A tiny prediction model from scratch",
        "Measuring how good a model is"]),
    (19, "Your Capstone Project", "Plan and build a complete program of your own.", "core", None, [
        "Planning the project", "Building the core logic", "Saving data and handling errors",
        "Polishing and sharing your work"]),
]
