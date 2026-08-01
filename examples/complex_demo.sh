#!/bin/bash

# A "complex" command that demonstrates:
# 1. Command substitution: $(...)
# 2. Pipes: |
# 3. Redirection: >, >>, 2>, &>
# 4. Process substitution: <(...)
# 5. Here strings: <<<
# 6. Arithmetic expansion: $((...))
# 7. Brace expansion: {a,b}

echo "--- Starting Complex Command Demo ---"

# Create a dummy file to work with
echo -e "apple\nbanana\ncherry\ndate\nelderberry\nfig\ngrape" > fruits.txt

# The "Monster" Command:
# 1. Take the current date and time.
# 2. Use brace expansion to create multiple dummy files.
# 3. Use process substitution to feed a list of fruits into a loop.
# 4. Use command substitution to count lines.
# 5. Use pipes to filter, sort, and transform.
# 6. Redirect stdout and stderr to different places.

{
  echo "Execution Timestamp: $(date)"
  echo "--------------------------------"
  
  # Use process substitution to read from a command output as if it were a file
  # and pipe it through several transformations
  cat <(sort fruits.txt | grep -E 'a|e') | \
    awk '{print toupper($0)}' | \
    sed 's/$/ [PROCESSED]/' | \
    tee processed_fruits.txt

  echo "--------------------------------"
  echo "Summary Statistics:"
  
  # Arithmetic expansion and command substitution
  echo "Total fruits processed: $(( $(wc -l < processed_fruits.txt) ))"
  
  # Using a here-string to pass data to a command
  while read -r line; do
    echo "Log entry: $line"
  done <<< "$(cat processed_fruits.txt)"

} &> execution_log.txt 2> error_log.txt

echo "Demo complete. Check 'processed_fruits.txt' and 'execution_log.txt'."
echo "Created dummy files using brace expansion:"
touch file_{1..3}.tmp

# Cleanup (optional, commented out so you can see the results)
# rm fruits.txt processed_fruits.txt execution_log.txt error_log.txt file_{1..3}.tmp
