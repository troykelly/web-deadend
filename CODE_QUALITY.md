# Code Quality Tools

This project uses industry-standard code quality tools to maintain clean, consistent, and type-safe Python code.

## Tools Overview

### 🎨 Black - Code Formatter
**Opinionated automatic code formatter** ("the uncompromising code formatter")

- Automatically formats code to PEP 8 standards
- Ensures consistent style across all Python files
- **Config:** `pyproject.toml` (`[tool.black]`)
- **Line length:** 100 characters
- **Target:** Python 3.13

**Usage:**
```bash
# Format all files
black src/ tests/

# Check formatting without changes
black --check --diff src/ tests/
```

---

### 📦 isort - Import Sorter
**Automatically sorts and organizes import statements**

- Groups imports: stdlib → third-party → local
- Compatible with Black (uses `--profile black`)
- Enforces consistent import ordering

**Usage:**
```bash
# Sort imports
isort src/ tests/

# Check without changes
isort --check-only --diff src/ tests/
```

---

### ✅ Flake8 - Linter
**Static analysis tool for code quality and PEP 8 compliance**

- Checks for syntax errors, undefined names, unused imports
- Enforces PEP 8 style guidelines
- **Config:** `.flake8`
- **Ignores:** E203, W503 (conflicts with Black), E722, F401, F841, E501

**Usage:**
```bash
# Lint all code
flake8 src/ tests/

# Show statistics
flake8 src/ tests/ --statistics
```

---

### 🔍 MyPy - Type Checker
**Static type checker for Python**

- Catches type-related bugs before runtime
- Enforces type hints for better code documentation
- **Config:** `pyproject.toml` (`[tool.mypy]`)
- **Excludes:** `tests/` directory

**Usage:**
```bash
# Type check source code
mypy src/

# Type check with verbose output
mypy src/ --show-error-codes
```

---

## Pre-Commit Hooks

**Automatically run code quality checks before each commit**

###  Installation

```bash
# Install pre-commit hooks (one-time setup)
pre-commit install
```

### What Gets Checked

Pre-commit runs these checks automatically on staged files:

1. ✅ **Trailing whitespace removal**
2. ✅ **End-of-file fixer**
3. ✅ **YAML syntax validation**
4. ✅ **Large file detection** (>1MB)
5. ✅ **Merge conflict detection**
6. ✅ **Private key detection**
7. 🎨 **Black** - Code formatting
8. 📦 **isort** - Import sorting
9. ✅ **Flake8** - Linting
10. 🔍 **MyPy** - Type checking (src/ only)

### Manual Execution

```bash
# Run all hooks on all files
pre-commit run --all-files

# Run specific hook
pre-commit run black --all-files
pre-commit run flake8 --all-files
pre-commit run mypy --all-files
```

### Bypassing Hooks (Not Recommended)

```bash
# Skip pre-commit hooks (emergency only!)
git commit --no-verify -m "message"
```

---

## GitHub Actions CI/CD

**Automated code quality checks on every push and pull request**

### Workflows

#### `.github/workflows/code-quality.yml`

**Runs on:** `push` to `main`, all pull requests

**Jobs:**
1. **code-quality** - Runs Black, isort, Flake8, MyPy
2. **tests** - Runs full test suite with coverage

**Matrix:** Python 3.13

**Features:**
- ✅ Dependency caching for faster runs
- ✅ Coverage reports (XML, HTML)
- ✅ Upload to Codecov
- ✅ Artifacts (coverage HTML report)

**Status:** Required checks (blocks merge if failing)

---

## Configuration Files

### `pyproject.toml`
Centralized configuration for:
- Black (formatting)
- isort (imports)
- MyPy (type checking)
- Pytest (testing)
- Coverage (code coverage)

### `.flake8`
Flake8-specific configuration:
- Max line length: 100
- Ignored rules: E203, W503, E722, F401, F841, E501
- Excluded directories: `.git`, `__pycache__`, `venv`, `build`, `dist`

### `.pre-commit-config.yaml`
Pre-commit hook definitions with specific tool versions

---

## Development Workflow

### Recommended Flow

1. **Make changes to code**

2. **Format and lint locally:**
   ```bash
   black src/ tests/
   isort src/ tests/
   flake8 src/ tests/
   mypy src/
   ```

3. **Run tests:**
   ```bash
   pytest tests/
   ```

4. **Commit** (pre-commit hooks run automatically):
   ```bash
   git add .
   git commit -m "Your message"
   # Pre-commit hooks run here
   ```

5. **Push** (triggers CI/CD):
   ```bash
   git push
   # GitHub Actions runs full suite
   ```

### Quick Check Script

```bash
# Run all checks locally (mimics CI)
black src/ tests/ && \
isort src/ tests/ && \
flake8 src/ tests/ && \
mypy src/ && \
pytest tests/ --cov=src
```

---

## IDE Integration

### VS Code

Install extensions:
- **Python** (ms-python.python)
- **Pylance** (ms-python.vscode-pylance)
- **Black Formatter** (ms-python.black-formatter)
- **isort** (ms-python.isort)
- **Flake8** (ms-python.flake8)
- **Mypy Type Checker** (ms-python.mypy-type-checker)

Add to `.vscode/settings.json`:
```json
{
  "python.formatting.provider": "black",
  "python.linting.enabled": true,
  "python.linting.flake8Enabled": true,
  "python.linting.mypyEnabled": true,
  "[python]": {
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  }
}
```

### PyCharm

1. **Black:**
   - Settings → Tools → File Watchers → Add Black
   - Or: Settings → Tools → External Tools

2. **Flake8:**
   - Settings → Tools → Python Integrated Tools → Linter → Flake8

3. **MyPy:**
   - Plugins → Install "Mypy" plugin
   - Settings → Mypy → Enable

---

## Troubleshooting

### Pre-commit hooks fail

**Solution:** Fix the issues reported, then:
```bash
# Re-run hooks to verify
pre-commit run --all-files

# Commit again
git commit -m "Fixed issues"
```

### Black and Flake8 conflict

**Solution:** This shouldn't happen with our config (.flake8 ignores E203, W503). If it does:
```bash
# Format with black first
black src/ tests/

# Then check flake8
flake8 src/ tests/
```

### MyPy type errors

**Solution:** Add type hints or use `# type: ignore` for unavoidable cases:
```python
result = some_function()  # type: ignore[some-error-code]
```

### Tests fail in CI but pass locally

**Solution:** Ensure dependencies match:
```bash
# Install exact versions
pip install -r requirements-dev.txt

# Re-run tests
pytest tests/
```

---

## Best Practices

✅ **DO:**
- Run `black` and `isort` before committing
- Add type hints to new functions
- Keep functions focused (single responsibility)
- Write tests for new code
- Review pre-commit output

❌ **DON'T:**
- Skip pre-commit hooks (unless emergency)
- Disable linting rules without good reason
- Commit code with failing tests
- Push directly to `main` without PR review

---

## Metrics

**Code Coverage Goal:** ≥ 90%

**Current Coverage:**
- Overall: 92%
- src/server.py: 89%
- src/response/handlers.py: 92%
- src/response/utils.py: 98%

**Test Count:** 103 tests (all passing)

---

## Additional Resources

- [Black Documentation](https://black.readthedocs.io/)
- [isort Documentation](https://pycqa.github.io/isort/)
- [Flake8 Documentation](https://flake8.pycqa.org/)
- [MyPy Documentation](https://mypy.readthedocs.io/)
- [Pre-commit Documentation](https://pre-commit.com/)
- [PEP 8 Style Guide](https://pep8.org/)
