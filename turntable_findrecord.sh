#!/bin/bash
################################################################################
# TURNTABLE RECORD FINDER WITH RANGE VIEWING, NAVIGATION, MONITORING, AND MORE
#
# New features:
# 1. Range viewing: view multiple records at once in various patterns
# 2. Sector_a now represents bitcoin keys with associated addresses
# 3. Energy wavepattern visualization
################################################################################

# Initialize tput colors
init_colors() {
    # Check if stdout is a terminal
    if [ -t 1 ]; then
        # Basic colors
        BLUE=$(tput setaf 4 || tput AF 4)
        YELLOW=$(tput setaf 3 || tput AF 3)
        RED=$(tput setaf 1 || tput AF 1)
        GREEN=$(tput setaf 2 || tput AF 2)
        MAGENTA=$(tput setaf 5 || tput AF 5)
        CYAN=$(tput setaf 6 || tput AF 6)
        BOLD=$(tput bold)
        RESET=$(tput sgr0)
    else
        # No colors if output is redirected
        BLUE=""; YELLOW=""; RED=""; GREEN=""; MAGENTA=""; CYAN=""; BOLD=""; RESET=""
    fi
    
    # Enhanced colors with bold
    BLUE_BOLD="${BOLD}${BLUE}"
    YELLOW_BOLD="${BOLD}${YELLOW}"
    RED_BOLD="${BOLD}${RED}"
    GREEN_BOLD="${BOLD}${GREEN}"
    MAGENTA_BOLD="${BOLD}${MAGENTA}"
    CYAN_BOLD="${BOLD}${CYAN}"
}

# ClickHouse server password configuration - use environment variable with fallback
CH_PASSWORD="${CH_PASSWORD:-asdf}"

# Initialize colors
init_colors

# Function to clear screen with minimal flicker
clear_screen() {
    printf "\033[2J\033[H"  # ANSI escape codes to clear screen and move to home position
}

################################################################################
# FUNCTION: show_help
# Displays comprehensive help information
################################################################################
show_help() {
    clear_screen
    echo ""
    echo "${BLUE_BOLD}======================================== TURNTABLE RECORD FINDER HELP ========================================${RESET}"
    echo ""
    echo "${YELLOW_BOLD}GLOBAL KEYBOARD SHORTCUTS:${RESET}"
    echo "  ${GREEN_BOLD}Ctrl+S${RESET}  - Save current record to file"
    echo "  ${GREEN_BOLD}Ctrl+Q${RESET}  - Quit program immediately"
    echo "  ${GREEN_BOLD}Ctrl+T${RESET}  - Jump to current time"
    echo ""
    echo "${YELLOW_BOLD}MAIN COMMANDS:${RESET}"
    echo "  ${GREEN_BOLD}id:<number>${RESET}        - Search by ID (e.g., id:123456)"
    echo "  ${GREEN_BOLD}<ordinal>${RESET}          - Search by ordinal (e.g., 873663)"
    echo "  ${GREEN_BOLD}<ordinal>u${RESET}         - Search by ordinal with untruncated output"
    echo "  ${GREEN_BOLD}range <spec>${RESET}       - View multiple records (e.g., range 874000-874003)"
    echo "  ${GREEN_BOLD}pattern <spec>${RESET}     - View non-sequential records (e.g., pattern 874002,874001,874000)"
    echo "  ${GREEN_BOLD}HH:MM${RESET}              - Search by time (today, e.g., 16:31)"
    echo "  ${GREEN_BOLD}YYYY-MM-DD${RESET}         - Search by date (e.g., 2023-05-15)"
    echo "  ${GREEN_BOLD}YYYYMMDDHHMMSS${RESET}     - Search by compact datetime"
    echo "  ${GREEN_BOLD}watch${RESET}              - Monitor current record (truncated output)"
    echo "  ${GREEN_BOLD}watchu${RESET}             - Monitor current record (untruncated output)"
    echo "  ${GREEN_BOLD}watch <ordinal>${RESET}    - Monitor specific record (truncated)"
    echo "  ${GREEN_BOLD}watchu <ordinal>${RESET}   - Monitor specific record (untruncated)"
    echo "  ${GREEN_BOLD}find <pattern>${RESET}     - Search records for text pattern"
    echo "  ${GREEN_BOLD}help${RESET} or ${GREEN_BOLD}h${RESET}       - Show this help screen"
    echo "  ${GREEN_BOLD}exit${RESET}               - Quit the program"
    echo ""
    echo "${YELLOW_BOLD}RANGE SPECIFICATION:${RESET}"
    echo "  ${GREEN_BOLD}Continuous:${RESET}    range 874000-874003"
    echo "  ${GREEN_BOLD}Broken up:${RESET}     range 874000,874002-874003"
    echo "  ${GREEN_BOLD}Non-seq:${RESET}       pattern 874002,874001,874000,874003"
    echo "  ${GREEN_BOLD}With step:${RESET}      range 874000-874010+2 (every 2nd record)"
    echo ""
    echo "${YELLOW_BOLD}NAVIGATION MODE COMMANDS:${RESET}"
    echo "  [n] Next record (ordinal+1)"
    echo "  [p] Previous record (ordinal-1)"
    echo "  [+] Next phase slot (+10 minutes)"
    echo "  [-] Previous phase slot (-10 minutes)"
    echo "  [g] Go to specific ordinal"
    echo "  [t] Go to current time"
    echo "  [b] Go back to previous record (history)"
    echo "  [f] Enter fixed monitoring mode for this record"
    echo "  [w] Enter watch mode for current record (follows time)"
    echo "  [e] Edit current record"
    echo "  [s] Save current record to file"
    echo "  [c] Export current record to CSV"
    echo "  [j] Show record in JSON format"
    echo "  [r] View range of records"
    echo "  [h] Show help"
    echo "  [q] Quit navigation"
    echo ""
    echo "${YELLOW_BOLD}ENERGY WAVEPATTERNS:${RESET}"
    echo "  sector_a: Bitcoin key with associated address"
    echo "  wavepattern: Resonating imploding energy signature"
    echo "  polarization: ${CYAN_BOLD}Positive${RESET} or ${MAGENTA_BOLD}Negative${RESET} magnetic surge at center"
    echo ""
    echo "${BLUE_BOLD}================================================================================${RESET}"
   # echo ""
}


# Check ClickHouse connection at startup
if ! clickhouse-client --password "$CH_PASSWORD" --query "SELECT 1" >/dev/null 2>&1; then
    echo "${RED_BOLD}Error: Failed to connect to ClickHouse server. Check your password and network connection.${RESET}" >&2
    exit 1
fi

################################################################################
# FUNCTION: safe_get_key
# Safely reads a single key with proper terminal handling
################################################################################
safe_get_key() {
    # Save current terminal settings
    old_stty=$(stty -g)
    # Set raw mode and disable echo
    stty raw -echo -icanon min 1 time 0
    # Read single character
    char=$(dd bs=1 count=1 2>/dev/null)
    # Restore terminal settings immediately
    stty "$old_stty"
    # Return the character
    printf "%s" "$char"
}

################################################################################
# FUNCTION: escape_sql_string
# Escapes single quotes for SQL queries
################################################################################
escape_sql_string() {
    echo "$1" | sed "s/'/''/g"
}

################################################################################
# FUNCTION: highlight_matches
# Highlights search patterns in text using tput colors
################################################################################
highlight_matches() {
    text="$1"
    pattern="$2"
    
    # Skip highlighting if no pattern or colors not available
    if [ -z "$pattern" ] || [ -z "$RED_BOLD" ]; then
        echo "$text"
        return
    fi
    
    # Escape pattern for sed
    safe_pattern=$(echo "$pattern" | sed 's/[][\.*/]/\\&/g')
    
    # Highlight pattern matches with red bold text
    echo "$text" | sed "s/$safe_pattern/${RED_BOLD}&${RESET}/gi"
}

################################################################################
# FUNCTION: parse_compact_datetime
# Parses compact datetime strings into standard format
################################################################################
parse_compact_datetime() {
    input="$1"
    len=$(echo -n "$input" | wc -c)
    
    if [ $len -eq 8 ]; then
        year=$(echo "$input" | cut -c1-4)
        month=$(echo "$input" | cut -c5-6)
        day=$(echo "$input" | cut -c7-8)
        datetime="$year-$month-$day 13:15:05"
    elif [ $len -eq 12 ]; then
        year=$(echo "$input" | cut -c1-4)
        month=$(echo "$input" | cut -c5-6)
        day=$(echo "$input" | cut -c7-8)
        hour=$(echo "$input" | cut -c9-10)
        minute=$(echo "$input" | cut -c11-12)
        datetime="$year-$month-$day $hour:$minute:00"
    elif [ $len -eq 14 ]; then
        year=$(echo "$input" | cut -c1-4)
        month=$(echo "$input" | cut -c5-6)
        day=$(echo "$input" | cut -c7-8)
        hour=$(echo "$input" | cut -c9-10)
        minute=$(echo "$input" | cut -c11-12)
        second=$(echo "$input" | cut -c13-14)
        datetime="$year-$month-$day $hour:$minute:$second"
    else
        echo "invalid"
        return
    fi
    
    # Validate the date
    if ! date -d "$datetime" >/dev/null 2>&1; then
        echo "invalid"
        return
    fi
    
    echo "$datetime"
}

################################################################################
# FUNCTION: has_u_suffix
# Checks if input ends with 'u' for untruncated output
################################################################################
has_u_suffix() {
    echo "$1" | grep -q 'u$'
}

################################################################################
# FUNCTION: remove_u_suffix
# Removes trailing 'u' from input string
################################################################################
remove_u_suffix() {
    echo "$1" | sed 's/u$//'
}

################################################################################
# FUNCTION: generate_trunc_expr
# Generates SQL expression for field truncation
################################################################################
generate_trunc_expr() {
    field="$1"
    echo "CASE 
        WHEN lengthUTF8($field) > 8 THEN 
            concat(substringUTF8($field, 1, 4), '..', substringUTF8($field, -4))
        ELSE $field
    END AS $field"
}

################################################################################
# FUNCTION: display_record
# Displays a record by ordinal with optional truncation
################################################################################
display_record() {
    ordinal="$1"
    truncate="$2"
    
    clear_screen
    
    if [ "$truncate" = true ]; then
        command_expr=$(generate_trunc_expr "command")
        comments_expr=$(generate_trunc_expr "comments")
        sector_a_expr=$(generate_trunc_expr "sector_a")
    else
        command_expr="command"
        comments_expr="comments"
        sector_a_expr="sector_a"
    fi

    query="SELECT
            id,
            ordinal,
            formatDateTime(toDateTime(id), '%H:%i') AS groove_time,
            round((ordinal % 144) * 2.5, 2) AS phase,
            $command_expr,
            concat(
                substring('MonTueWedThuFriSatSun', (toDayOfWeek(toDateTime(id)) * 3) - 2, 3),
                ' ',
                substring('JanFebMarAprMayJunJulAugSepOctNovDec', (toMonth(toDateTime(id)) * 3) - 2, 3),
                ' ',
                toString(toDayOfMonth(toDateTime(id))),
                ' ',
                toString(toYear(toDateTime(id)))
            ) AS day_date,
            $comments_expr,
            $sector_a_expr,
            phase_a,
            sector_b,
            phase_b
        FROM gamma_data 
        WHERE ordinal = $ordinal"
    
    clickhouse-client --password "$CH_PASSWORD" --format prettyCompact --query "$query"
}

################################################################################
# FUNCTION: get_current_ordinal
# Gets the ordinal for the current time (most recent past record)
################################################################################
get_current_ordinal() {
    current_timestamp=$(date +%s)
    
    query="SELECT ordinal 
           FROM gamma_data 
           WHERE id <= $current_timestamp 
           ORDER BY id DESC 
           LIMIT 1"
           
    clickhouse-client --password "$CH_PASSWORD" --query "$query"
}

################################################################################
# FUNCTION: get_ordinal_by_timestamp
# Gets the closest ordinal for a specific timestamp
################################################################################
get_ordinal_by_timestamp() {
    timestamp="$1"
    query="SELECT ordinal 
           FROM gamma_data 
           ORDER BY abs(id - $timestamp) ASC 
           LIMIT 1"
           
    clickhouse-client --password "$CH_PASSWORD" --query "$query"
}

################################################################################
# FUNCTION: read_key_nonblocking
# Reads a key in non-blocking mode
################################################################################
read_key_nonblocking() {
    # Save current terminal settings
    old_stty=$(stty -g)
    # Set non-blocking raw mode
    stty raw -echo -icanon min 0 time 0
    # Read single character
    char=$(dd bs=1 count=1 2>/dev/null)
    # Restore terminal settings
    stty "$old_stty"
    # Return the character
    printf "%s" "$char"
}

################################################################################
# FUNCTION: monitor_record
# Real-time monitoring of any record
# Input:
#   $1 - Ordinal or "current" for current record
#   $2 - Truncation flag (true/false)
################################################################################

monitor_record() {
    target="$1"
    truncate_flag="$2"
    
    # Save current terminal settings
    original_tty=$(stty -g)
    
    clear_screen
    
    last_ordinal=0
    iteration_count=0
    stage=1  # 1: 1s refresh, 2: 10s refresh, 3: 60s refresh
    
    # Determine if we're monitoring current record or a fixed record
    if [ "$target" = "current" ]; then
        mode="current"
        mode_description="CURRENT RECORD"
    else
        mode="fixed"
        fixed_ordinal="$target"
        mode_description="FIXED RECORD: $fixed_ordinal"
    fi
    
    while true; do
        # Set refresh rate based on current stage
        case $stage in
            1) refresh_rate=1 ;;
            2) refresh_rate=7 ;; #10
            3) refresh_rate=7 ;; #60
        esac
        
        # Get current ordinal based on mode
        if [ "$mode" = "current" ]; then
            current_ordinal=$(get_current_ordinal)
            if [ -z "$current_ordinal" ]; then
                echo "Error: Could not get current ordinal"
                break
            fi
            ordinal="$current_ordinal"
        else
            ordinal="$fixed_ordinal"
        fi
        
        clear_screen
            
        # Display monitoring header
        current_time=$(date +"%Y-%m-%d %H:%M:%S")
        echo "${BLUE_BOLD}"
        echo "================================================================================="
        echo " MONITORING $mode_description - STAGE $stage: REFRESHING EVERY $refresh_rate SECOND(S)"
        echo " Press 'q' to exit monitoring mode"
        echo " Press ${GREEN_BOLD}Ctrl+L${RESET} to clear screen"
        echo " Press ANY KEY to reset to 1-second refresh"
        echo " Current time: $current_time"
        echo "================================================================================="
        echo "${RESET}"
        
        # Display the record
        display_record "$ordinal" "$truncate_flag"
        
        # Display refresh indicator
        # echo "${YELLOW_BOLD}"
	# echo "--------------------------------------------------------"
        # printf "Refocusing in %s second(s)... Stage: %s, Iteration: %s/10 " \
        #       "$refresh_rate" "$stage" "$iteration_count"
        # echo "${RESET}"
        
        # Check for quit key without blocking
        key_pressed=""
        end_time=$(( $(date +%s) + refresh_rate ))
        reset_requested=false
        
        while [ $(date +%s) -lt $end_time ]; do
            # Read key non-blocking
            char=$(read_key_nonblocking)
            
            if [ "$char" = "q" ]; then
                key_pressed="q"
                break
            elif [ "$char" = $'\x11' ]; then  # Ctrl+Q
                key_pressed="ctrl_q"
                break
            elif [ "$char" = $'\x0c' ]; then  # Ctrl+L
                clear_screen
                break
            elif [ -n "$char" ]; then  # Any other key
                reset_requested=true
                break
            fi
            
            # Update countdown
            remaining=$((end_time - $(date +%s)))
            if [ $remaining -ge 0 ]; then
                printf "\033[7;0H\033[K"  # Move to refresh line and clear
                printf "${GREEN_BOLD}Refreshing in %s second(s)... Stage: %s, Iteration: %s/10 " \
                       "$remaining" "$stage" "$iteration_count"
                printf "${RESET}"
            fi
            
            # Coast for a short interval to prevent high CPU
            sleep 0.05
        done
        
        # Handle key press results
        if [ "$key_pressed" = "q" ]; then
            break
        elif [ "$key_pressed" = "ctrl_q" ]; then
            # Restore terminal settings
            stty "$original_tty"
            echo "Exiting program..."
            exit 0
        fi
        
        # Handle reset request (any key press)
        if [ "$reset_requested" = true ]; then
            stage=1
            iteration_count=0
            continue
        fi
        
        # Update progression state
        iteration_count=$((iteration_count + 1))
        
        # Advance to next stage if iteration count reached
        if [ $stage -eq 1 ] && [ $iteration_count -ge 10 ]; then
            stage=2
            iteration_count=0
        elif [ $stage -eq 2 ] && [ $iteration_count -ge 10 ]; then
            stage=3
            iteration_count=0
        fi
    done
    
    # Restore terminal settings
    stty "$original_tty"
    clear_screen
    echo "Exited monitoring mode"
}


################################################################################
# FUNCTION: save_current_record
# Saves the current record to a file
# Input:
#   $1 - Ordinal of the record to save
################################################################################
save_current_record() {
    ordinal="$1"
    filename="record_${ordinal}_$(date +%Y%m%d_%H%M%S).txt"
    
    # Get full record without truncation
    query="SELECT * FROM gamma_data WHERE ordinal = $ordinal"
    
    # Save to file
    clickhouse-client --password "$CH_PASSWORD" --format PrettyCompact --query "$query" > "$filename"
    
    clear_screen
    echo "Record saved to: $filename"
    echo "File content:"
    cat "$filename"
}

################################################################################
# FUNCTION: export_record_to_csv
# Exports the current record to CSV
# Input:
#   $1 - Ordinal of the record to export
################################################################################
export_record_to_csv() {
    ordinal="$1"
    filename="record_${ordinal}_$(date +%Y%m%d_%H%M%S).csv"
    
    # Export to CSV
    query="SELECT * FROM gamma_data WHERE ordinal = $ordinal FORMAT CSV"
    
    # Save to file
    clickhouse-client --password "$CH_PASSWORD" --query "$query" > "$filename"
    
    clear_screen
    echo "Record exported to CSV: $filename"
    echo "File content:"
    cat "$filename"
}

################################################################################
# FUNCTION: show_json_record
# Displays the record in JSON format
# Input:
#   $1 - Ordinal of the record to display
################################################################################
show_json_record() {
    ordinal="$1"
    
    # Get record in JSON format
    query="SELECT * FROM gamma_data WHERE ordinal = $ordinal FORMAT JSONEachRow"
    json_output=$(clickhouse-client --password "$CH_PASSWORD" --query "$query")
    
    clear_screen
    echo "Record $ordinal in JSON format:"
    
    # Try to pretty-print with jq if available
    if command -v jq >/dev/null 2>&1; then
        echo "$json_output" | jq .
    else
        echo "$json_output"
        echo ""
        echo "Note: Install 'jq' for formatted JSON output"
    fi
}

################################################################################
# FUNCTION: edit_record_field
# Edits a specific field of the current record
# Input:
#   $1 - Ordinal of the record to edit
#   $2 - Truncation flag (true/false)
################################################################################
edit_record_field() {
    ordinal="$1"
    truncate_flag="$2"
    
    clear_screen
    # Display field selection prompt
    echo "------------------------------------------"
    echo "Editable fields:"
    echo "  1. command"
    echo "  2. comments"
    echo "  3. sector_a"
    echo "------------------------------------------"
    printf "Enter field number to edit (1-3) or 'c' to cancel: "
    
    read -r field_choice
    case "$field_choice" in
        1) field="command" ;;
        2) field="comments" ;;
        3) field="sector_a" ;;
        c|C) 
            echo "Edit cancelled"
            return 
            ;;
        *) 
            echo "Invalid selection: '$field_choice'"
            return 1
            ;;
    esac
    
    # Get current value for reference
    current_value_query="SELECT $field FROM gamma_data WHERE ordinal = $ordinal"
    current_value=$(clickhouse-client --password "$CH_PASSWORD" --query "$current_value_query")
    
    # Display current value and prompt for new value
    echo "------------------------------------------"
    echo "Editing field: $field"
    echo "Current value: '$current_value'"
    printf "Enter new value: "
    
    # Read new value with support for multi-line input
    new_value=""
    while IFS= read -r line; do
        # Break on empty line (double Enter)
        [ -z "$line" ] && break
        if [ -z "$new_value" ]; then
            new_value="$line"
        else
            new_value="$new_value"$'\n'"$line"
        fi
    done
    
    # Escape single quotes for SQL
    escaped_value=$(escape_sql_string "$new_value")
    
    # Confirm before update
    clear_screen
    echo "------------------------------------------"
    echo "New value: '$new_value'"
    printf "Confirm update? [y/N] "
    read -r confirm
    
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Update cancelled"
        return
    fi
    
    # Execute update query
    update_query="ALTER TABLE gamma_data UPDATE $field = '$escaped_value' WHERE ordinal = $ordinal"
    if clickhouse-client --password "$CH_PASSWORD" --query "$update_query"; then
        echo "Field updated successfully!"
        
        # Wait for mutation to complete (up to 5 seconds)
        echo "Waiting for mutation to apply..."
        mutation_check="SELECT count() FROM system.mutations WHERE table = 'gamma_data' AND is_done = 0"
        attempts=0
        max_attempts=50  # 50 * 0.1 = 5 seconds
        
        while [ "$(clickhouse-client --password "$CH_PASSWORD" --query "$mutation_check")" -gt 0 ]; do
            sleep 0.1
            attempts=$((attempts + 1))
            if [ $attempts -ge $max_attempts ]; then
                echo "Warning: Mutation taking longer than expected. Data may not be immediately visible."
                break
            fi
        done
        
        # Display updated record
        display_record "$ordinal" "$truncate_flag"
    else
        echo "Error: Failed to update field"
    fi
}

################################################################################
# FUNCTION: search_records
# Searches records by content pattern with field-specific support
# Input:
#   $1 - Search pattern
################################################################################
search_records() {
    pattern="$1"
    
    clear_screen
    
    # Handle field-specific searches
    if echo "$pattern" | grep -q '^command:'; then
        field_pattern=$(echo "$pattern" | cut -d':' -f2-)
        if [ "$field_pattern" = "!" ]; then
            echo "Searching for non-empty command fields"
            condition="command != ''"
        else
            echo "Searching command field for: '$field_pattern'"
            safe_pattern=$(echo "$field_pattern" | sed "s/'/''/g")
            condition="command ILIKE '%$safe_pattern%'"
        fi
        
    elif echo "$pattern" | grep -q '^comments:'; then
        field_pattern=$(echo "$pattern" | cut -d':' -f2-)
        if [ "$field_pattern" = "!" ]; then
            echo "Searching for non-empty comments fields"
            condition="comments != ''"
        else
            echo "Searching comments field for: '$field_pattern'"
            safe_pattern=$(echo "$field_pattern" | sed "s/'/''/g")
            condition="comments ILIKE '%$safe_pattern%'"
        fi
        
    elif echo "$pattern" | grep -q '^sector_a:'; then
        field_pattern=$(echo "$pattern" | cut -d':' -f2-)
        if [ "$field_pattern" = "!" ]; then
            echo "Searching for non-empty sector_a fields"
            condition="sector_a != ''"
        else
            echo "Searching sector_a field for: '$field_pattern'"
            safe_pattern=$(echo "$field_pattern" | sed "s/'/''/g")
            condition="sector_a ILIKE '%$safe_pattern%'"
        fi
        
    else
        echo "Searching records for pattern: '$pattern'"
        safe_pattern=$(echo "$pattern" | sed "s/'/''/g")
        condition="command ILIKE '%$safe_pattern%' OR 
                   comments ILIKE '%$safe_pattern%' OR 
                   sector_a ILIKE '%$safe_pattern%'"
    fi

    # Build search query
    query="SELECT ordinal, command, comments, sector_a
           FROM gamma_data 
           WHERE $condition
           ORDER BY id DESC 
           LIMIT 100"
    
    # Execute query and process results
    found=0
    clickhouse-client --password "$CH_PASSWORD" --format TSV --query "$query" | while IFS=$'\t' read -r ordinal command_val comments_val sector_a_val; do
        # Skip header
        if [ "$ordinal" = "ordinal" ]; then
            continue
        fi
        
        # Initialize matched field and value
        matched_field=""
        matched_value=""
        
        # For field-specific searches, we know which field matched
        if echo "$pattern" | grep -q '^command:'; then
            if [ "$field_pattern" = "!" ]; then
                matched_field="command"
                matched_value="$command_val"
            else
                matched_field="command"
                matched_value=$(highlight_matches "$command_val" "$field_pattern")
            fi
            
        elif echo "$pattern" | grep -q '^comments:'; then
            if [ "$field_pattern" = "!" ]; then
                matched_field="comments"
                matched_value="$comments_val"
            else
                matched_field="comments"
                matched_value=$(highlight_matches "$comments_val" "$field_pattern")
            fi
            
        elif echo "$pattern" | grep -q '^sector_a:'; then
            if [ "$field_pattern" = "!" ]; then
                matched_field="sector_a"
                matched_value="$sector_a_val"
            else
                matched_field="sector_a"
                matched_value=$(highlight_matches "$sector_a_val" "$field_pattern")
            fi
            
        else
            # Generic search - check all fields
            if echo "$command_val" | grep -q -i "$pattern"; then
                matched_field="command"
                matched_value=$(highlight_matches "$command_val" "$pattern")
            elif echo "$comments_val" | grep -q -i "$pattern"; then
                matched_field="comments"
                matched_value=$(highlight_matches "$comments_val" "$pattern")
            elif echo "$sector_a_val" | grep -q -i "$pattern"; then
                matched_field="sector_a"
                matched_value=$(highlight_matches "$sector_a_val" "$pattern")
            fi
        fi
        
        # Print result
        if [ -n "$matched_field" ]; then
            # Print header only for first match
            if [ $found -eq 0 ]; then
                printf "┌─────────┬──────────────┬──────────────────────────────────────────────────────┐\n"
                printf "│ %-7s │ %-12s │ %-52s │\n" "ordinal" "field" "match"
                printf "├─────────┼──────────────┼──────────────────────────────────────────────────────┤\n"
            fi
            
            # Truncate long values to 50 characters
            truncated_value=$(echo "$matched_value" | cut -c1-50)
            if [ ${#matched_value} -gt 50 ]; then
                truncated_value="$truncated_value..."
            fi
            
            printf "│ %-7s │ %-12s │ %-52s │\n" "$ordinal" "$matched_field" "$truncated_value"
            found=$((found + 1))
        fi
    done
    
    # Print footer if results were found
    if [ $found -gt 0 ]; then
        printf "└─────────┴──────────────┴──────────────────────────────────────────────────────┘\n"
        echo "Found $found matching records (displaying up to 100 most recent)"
    else
        echo "No matching records found"
    fi
}

################################################################################
# FUNCTION: display_range
# Displays multiple records in a range
# Input:
#   $1 - Range specification (e.g., "874000-874003" or "874000,874002,874003")
#   $2 - Truncation flag (true/false)
################################################################################
display_range() {
    range_spec="$1"
    truncate_flag="$2"
    
    clear_screen
    echo "${CYAN_BOLD}Preparing range view...${RESET}"
    
    # Parse range specification
    IFS=',' read -ra segments <<< "$range_spec"
    ordinals=()
    
    for segment in "${segments[@]}"; do
        if [[ $segment == *-* ]]; then
            # Handle range segment (e.g., 874000-874003)
            start_end=(${segment//-/ })
            start=${start_end[0]}
            end=${start_end[1]}
            
            # Check for step value (e.g., 874000-874010+2)
            if [[ $end == *+* ]]; then
                step_end=(${end//+/ })
                end=${step_end[0]}
                step=${step_end[1]}
            else
                step=1
            fi
            
            # Validate
            if ! [[ $start =~ ^[0-9]+$ ]] || ! [[ $end =~ ^[0-9]+$ ]] || ! [[ $step =~ ^[0-9]+$ ]]; then
                echo "Invalid range segment: $segment"
                return 1
            fi
            
            # Generate sequence
            for ((i=start; i<=end; i+=step)); do
                ordinals+=("$i")
            done
        else
            # Handle single ordinal
            if [[ $segment =~ ^[0-9]+$ ]]; then
                ordinals+=("$segment")
            else
                echo "Invalid ordinal: $segment"
                return 1
            fi
        fi
    done

    # Limit to 20 records for performance
    if [ ${#ordinals[@]} -gt 20 ]; then
        echo "${YELLOW_BOLD}Too many records (${#ordinals[@]}). Displaying first 20.${RESET}"
        ordinals=("${ordinals[@]:0:20}")
    fi

    # Display header
    echo "${BLUE_BOLD}=== DISPLAYING ${#ordinals[@]} RECORDS ===${RESET}"
    echo ""
    
    # Display each record
    for ordinal in "${ordinals[@]}"; do
        echo "${GREEN_BOLD}--- Record $ordinal ---${RESET}"
        display_record "$ordinal" "$truncate_flag"
        echo ""
        
        # Visualize energy wavepattern
        visualize_wavepattern "$ordinal"
    done
}

################################################################################
# FUNCTION: visualize_wavepattern
# Visualizes the energy wavepattern for a record
# Input:
#   $1 - Ordinal of the record
################################################################################
visualize_wavepattern() {
    ordinal="$1"
    
    # Get sector_a data (bitcoin key)
    query="SELECT sector_a FROM gamma_data WHERE ordinal = $ordinal"
    sector_a=$(clickhouse-client --password "$CH_PASSWORD" --query "$query")
    
    # Generate unique hash for visualization
    hash=$(echo -n "$sector_a" | md5sum | cut -c1-16)
    
    # Determine polarization (positive/negative)
    last_digit=${hash: -1}
    if [[ $((0x$last_digit % 2)) -eq 0 ]]; then
        polarity="${CYAN_BOLD}Positive${RESET}"
        color=$CYAN
    else
        polarity="${MAGENTA_BOLD}Negative${RESET}"
        color=$MAGENTA
    fi
    
    # Generate wavepattern visualization
    echo "Energy Wavepattern: $polarity"
    echo "Signature: ${hash:0:8}-${hash:8:8}"
    
    # Visual representation
    for ((i=0; i<16; i+=2)); do
        # Get two hex digits
        hex_pair=${hash:i:2}
        
        # Convert to decimal (0-255)
        dec_val=$((0x$hex_pair))
        
        # Scale to 1-40 for visualization
        width=$((dec_val % 20 + 10))
        
        # Create wave line
        line=""
        for ((j=0; j<width; j++)); do
            # Use different characters for positive/negative
            if [[ $polarity == *Positive* ]]; then
                line+="▲"
            else
                line+="▼"
            fi
        done
        
        # Print with color
        printf "${color}%s${RESET}\n" "$line"
    done
    echo ""
}



################################################################################
# FUNCTION: navigate_records
# Provides navigation controls after displaying a record
################################################################################
navigate_records() {
    current_ordinal="$1"
    truncate_flag="$2"
    
    # Initialize history array
    history=("$current_ordinal")
    history_index=0
    
    while true; do
        # Display navigation options
        echo "------------------------------------------"
	echo "N|P +/- G T Back(history) Fixed W E S|C|J R Help Q"
       # echo "Navigation options:"
       # echo "  [n] Next record (ordinal+1)"
       # echo "  [p] Previous record (ordinal-1)"
       # echo "  [+] Next phase slot (+10 minutes)"
       # echo "  [-] Previous phase slot (-10 minutes)"
       # echo "  [g] Go to specific ordinal"
       # echo "  [t] Go to current time"
       # echo "  [b] Go back to previous record (history)"
       # echo "  [f] Enter fixed monitoring mode for this record"
       # echo "  [w] Enter watch mode for current record (follows time)"
       # echo "  [e] Edit current record"
       # echo "  [s] Save current record to file"
       # echo "  [c] Export current record to CSV"
       # echo "  [j] Show record in JSON format"
       # echo "  [r] View range of records"
       # echo "  [h] Show help"
       # echo "  [q] Quit navigation"
       # echo "------------------------------------------"
        printf "Enter navigation command: "
        
        # Read single character safely
        char_input=$(safe_get_key)
        echo "$char_input"  # Show what was pressed
        
        # Handle Ctrl sequences
        case "$char_input" in
            $'\x14') # Ctrl+T
                command="t"
                ;;
            $'\x13') # Ctrl+S
                command="s"
                ;;
            $'\x11') # Ctrl+Q
                command="q"
                ;;
            *)
                command="$char_input"
                ;;
        esac
        
        # Convert to lowercase
        command=$(echo "$command" | tr '[:upper:]' '[:lower:]')
        
        case $command in
            n)  new_ordinal=$((current_ordinal + 1))
                echo "Moving to next record: $new_ordinal"
                # Add to history
                history_index=$((history_index + 1))
                history=("${history[@]:0:$history_index}" "$new_ordinal")
                display_record "$new_ordinal" "$truncate_flag"
                current_ordinal="$new_ordinal"
                ;;
            p)  new_ordinal=$((current_ordinal - 1))
                echo "Moving to previous record: $new_ordinal"
                # Add to history
                history_index=$((history_index + 1))
                history=("${history[@]:0:$history_index}" "$new_ordinal")
                display_record "$new_ordinal" "$truncate_flag"
                current_ordinal="$new_ordinal"
                ;;
            +)  new_ordinal=$((current_ordinal + 1))
                echo "Moving to next phase slot: $new_ordinal"
                # Add to history
                history_index=$((history_index + 1))
                history=("${history[@]:0:$history_index}" "$new_ordinal")
                display_record "$new_ordinal" "$truncate_flag"
                current_ordinal="$new_ordinal"
                ;;
            -)  new_ordinal=$((current_ordinal - 1))
                echo "Moving to previous phase slot: $new_ordinal"
                # Add to history
                history_index=$((history_index + 1))
                history=("${history[@]:0:$history_index}" "$new_ordinal")
                display_record "$new_ordinal" "$truncate_flag"
                current_ordinal="$new_ordinal"
                ;;
            g)  clear_screen
                echo -n "Enter ordinal: "
                read new_ordinal
                if ! echo "$new_ordinal" | grep -q '^[0-9]\+$'; then
                    echo "Error: Ordinal must be an integer"
                    continue
                fi
                # Add to history
                history_index=$((history_index + 1))
                history=("${history[@]:0:$history_index}" "$new_ordinal")
                display_record "$new_ordinal" "$truncate_flag"
                current_ordinal="$new_ordinal"
                ;;
            t)  new_ordinal=$(get_current_ordinal)
                if [ -z "$new_ordinal" ]; then
                    echo "Error: Could not get current ordinal"
                    continue
                fi
                echo "Moving to current time: $new_ordinal"
                # Add to history
                history_index=$((history_index + 1))
                history=("${history[@]:0:$history_index}" "$new_ordinal")
                display_record "$new_ordinal" "$truncate_flag"
                current_ordinal="$new_ordinal"
                ;;
            b)  # Go back in history
                if [ $history_index -gt 0 ]; then
                    history_index=$((history_index - 1))
                    new_ordinal=${history[$history_index]}
                    echo "Going back to record: $new_ordinal"
                    display_record "$new_ordinal" "$truncate_flag"
                    current_ordinal="$new_ordinal"
                else
                    echo "Already at the beginning of history"
                fi
                ;;
            f)  # Fixed monitoring for the current viewed record
                echo "Entering fixed monitoring mode for record: $current_ordinal..."
                monitor_record "$current_ordinal" "$truncate_flag"
                # After exiting monitoring, redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
	    w)  # Watch the current record (which changes with time)
		echo "Entering watch mode for current record (follows time)..."
		monitor_record "current" "$truncate_flag"
		# Reset terminal to ensure proper formatting after monitoring
		stty sane
		# After exiting monitoring, redisplay current record
		display_record "$current_ordinal" "$truncate_flag"
		continue
		;;
            r)  # View range of records
                clear_screen
                echo -n "Enter range (e.g., 874000-874003 or 874000,874002): "
                read range_input
                display_range "$range_input" "$truncate_flag"
                
                # After displaying range, return to current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;                
            e)  # Edit current record
                echo "Editing record $current_ordinal..."
                edit_record_field "$current_ordinal" "$truncate_flag"
                # After editing, redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
            s)  # Save current record
                echo "Saving record $current_ordinal..."
                save_current_record "$current_ordinal"
                # After saving, redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
            c)  # Export to CSV
                echo "Exporting record $current_ordinal to CSV..."
                export_record_to_csv "$current_ordinal"
                # After exporting, redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
            j)  # Show JSON format
                echo "Showing record $current_ordinal in JSON format..."
                show_json_record "$current_ordinal"
                # After showing JSON, redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
            h)  # Show help
                show_help
                # After help, redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
            q)  # Quit navigation and clear screen
                clear_screen
                return 
                ;;
            *)  # Clear screen for invalid commands
                clear_screen
                echo "Invalid command: '$command'"
                # Redisplay current record
                display_record "$current_ordinal" "$truncate_flag"
                continue
                ;;
        esac
    done
}

################################################################################
# FUNCTION: find_closest_record
# Main record lookup function with input processing
################################################################################
find_closest_record() {
    input="$1"
    truncate=true
    
    clear_screen
    
    # Handle new range commands
    if [[ "$input" == range* ]] || [[ "$input" == pattern* ]]; then
        # Extract range specification
        range_spec="${input#* }"
        
        # Check for untruncated version
        if [[ "$input" == rangeu* ]]; then
            truncate=false
            range_spec="${input#rangeu }"
        elif [[ "$input" == patternu* ]]; then
            truncate=false
            range_spec="${input#patternu }"
        fi
        
        display_range "$range_spec" "$truncate"
        return
    fi
     
    # Handle help command
    if [ "$input" = "help" ] || [ "$input" = "h" ]; then
        show_help
        return
    fi
    
    # Handle special commands
    if [ "$input" = "watch" ]; then
        echo "Entering monitoring mode for current record..."
        monitor_record "current" true
        return
    elif [ "$input" = "watchu" ]; then
        echo "Entering monitoring mode for current record (untruncated)..."
        monitor_record "current" false
        return
    fi
    
    # Handle watch with ordinal
    if echo "$input" | grep -q '^watch '; then
        ordinal_part=$(echo "$input" | cut -d' ' -f2)
        if echo "$ordinal_part" | grep -q '^[0-9]\+$'; then
            echo "Entering monitoring mode for record: $ordinal_part..."
            monitor_record "$ordinal_part" true
            return
        else
            echo "Invalid ordinal: $ordinal_part"
            return 1
        fi
    elif echo "$input" | grep -q '^watchu '; then
        ordinal_part=$(echo "$input" | cut -d' ' -f2)
        if echo "$ordinal_part" | grep -q '^[0-9]\+$'; then
            echo "Entering monitoring mode for record: $ordinal_part (untruncated)..."
            monitor_record "$ordinal_part" false
            return
        else
            echo "Invalid ordinal: $ordinal_part"
            return 1
        fi
    fi
    
    # Handle search commands
    if echo "$input" | grep -q '^find '; then
        search_pattern=$(echo "$input" | cut -d' ' -f2-)
        if [ -z "$search_pattern" ]; then
            echo "Error: Search pattern cannot be empty"
            return 1
        fi
        search_records "$search_pattern"
        return
    fi
    
    # Handle empty input - use current time
    if [ -z "$input" ]; then
        echo "Using current time"
        ordinal=$(get_current_ordinal)
        if [ -z "$ordinal" ]; then
            echo "Error: Could not get current ordinal"
            return 1
        fi
        display_record "$ordinal" true
        navigate_records "$ordinal" true
        return
    fi
    
    # Check for u suffix
    if has_u_suffix "$input"; then
        truncate=false
        input=$(remove_u_suffix "$input")
    fi
    
    len=$(echo -n "$input" | wc -c)
    
    if echo "$input" | grep -q '^[0-9]\{8,14\}$'; then
        datetime=$(parse_compact_datetime "$input")
        if [ "$datetime" = "invalid" ]; then
            echo "Error: Invalid compact datetime format"
            return 1
        fi
        echo "Treating input as datetime: $datetime"
        timestamp=$(date -d "$datetime" +%s 2>/dev/null)
        if [ -z "$timestamp" ]; then
            echo "Error: Invalid datetime"
            return 1
        fi
        ordinal=$(get_ordinal_by_timestamp "$timestamp")
        
    elif echo "$input" | grep -q '^id:[0-9][0-9]*$'; then
        id_value=$(echo "$input" | cut -d':' -f2)
        echo "Treating input as ID: $id_value"
        query="SELECT ordinal FROM gamma_data WHERE id = $id_value LIMIT 1"
        ordinal=$(clickhouse-client --password "$CH_PASSWORD" --query "$query")
        
    elif echo "$input" | grep -q '^[0-9][0-9]*$'; then
        echo "Treating input as ordinal: $input"
        query="SELECT ordinal FROM gamma_data WHERE ordinal = $input LIMIT 1"
        ordinal=$(clickhouse-client --password "$CH_PASSWORD" --query "$query")
        
    elif echo "$input" | grep -q '^[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}$'; then
        echo "Treating input as date: $input (assuming 13:15:05)"
        datetime="$input 13:15:05"
        timestamp=$(date -d "$datetime" +%s 2>/dev/null)
        if [ -z "$timestamp" ]; then
            echo "Error: Invalid date format"
            return 1
        fi
        ordinal=$(get_ordinal_by_timestamp "$timestamp")
        
    elif echo "$input" | grep -q '^[0-9]\{2\}:[0-9]\{2\}\(:[0-9]\{2\}\)\?$'; then
        echo "Treating input as time: $input (today)"
        current_date=$(date +%Y-%m-%d)
        datetime="$current_date $input"
        timestamp=$(date -d "$datetime" +%s 2>/dev/null)
        if [ -z "$timestamp" ]; then
            echo "Error: Invalid time format"
            return 1
        fi
        ordinal=$(get_ordinal_by_timestamp "$timestamp")
        
    elif echo "$input" | grep -q '^[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}[T ][0-9]\{2\}:[0-9]\{2\}'; then
        echo "Treating input as datetime: $input"
        datetime=$(echo "$input" | tr 'T' ' ')
        timestamp=$(date -d "$datetime" +%s 2>/dev/null)
        if [ -z "$timestamp" ]; then
            echo "Error: Invalid datetime format"
            return 1
        fi
        ordinal=$(get_ordinal_by_timestamp "$timestamp")
        
    elif [ "$input" = "exit" ]; then
        echo "Exiting..."
        exit 0
    else
        echo "Invalid input format"
        echo "Type ${GREEN_BOLD}help${RESET} for available commands"
        return 1
    fi

    if [ -z "$ordinal" ]; then
        echo "Error: No matching record found"
        return 1
    fi
    
    display_record "$ordinal" "$truncate"
    navigate_records "$ordinal" "$truncate"
}

################################################################################
# MAIN INTERACTIVE LOOP
################################################################################
# Initialize colors
init_colors

clear_screen
echo "${BLUE_BOLD}Turntable Record Finder with Navigation, Monitoring, Editing, Search, and Help${RESET}"
echo "Type ${GREEN_BOLD}help${RESET} for assistance, ${GREEN_BOLD}exit${RESET} to quit"
echo "------------------------------------------"

while true; do
    current_human=$(date +"%Y-%m-%d %H:%M:%S")
    
    printf "[%s] Time requested: " "$current_human"
    read input
    
    find_closest_record "$input"
done
