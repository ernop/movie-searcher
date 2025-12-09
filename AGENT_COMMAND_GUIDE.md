# Agent Command Execution Guide

## Critical Finding: Command Execution Reliability

**Problem**: PowerShell wrapper commands and complex escaping cause commands to hang indefinitely in Cursor's terminal tool.

**Solution**: Use direct command execution without PowerShell wrappers.

## Working Method

### ✅ DO THIS (Always Works)
```powershell
.\venv\Scripts\python.exe -m ruff check . --statistics
```

### ❌ DON'T DO THIS (Hangs)
```powershell
powershell -NoLogo -NoProfile -Command "complex escaping here"
```

## Rules for Reliable Command Execution

1. **Direct execution**: Run commands directly, not wrapped in PowerShell `-Command` strings
2. **Simple redirection**: Use `>` for file output, avoid complex pipelines
3. **No escaping**: Don't escape `$` or other characters unless absolutely necessary
4. **Background jobs**: Only use `is_background=true` for long-running processes that should continue after tool returns

## Examples

### Running Python tools
```powershell
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m ruff check . --fix
.\venv\Scripts\python.exe -m pip install package
```

### Redirecting output
```powershell
.\venv\Scripts\python.exe -m ruff check . > output.txt 2>&1
```

### If you MUST use PowerShell features
Use a simple script file instead of inline `-Command` strings:
```powershell
# Create script.ps1, then:
powershell -File script.ps1
```

## When Commands Hang

If a command hangs:
1. **Restart Cursor** - This often fixes terminal state issues
2. **Check for PowerShell wrappers** - Remove any `powershell -Command` wrappers
3. **Simplify the command** - Break complex commands into simpler steps
4. **Use file redirection** - Instead of piping, write to files and read them

## Testing Command Execution

Before running complex commands, test with a simple one:
```powershell
Get-Date
```

If that works, proceed. If it hangs, restart Cursor.







