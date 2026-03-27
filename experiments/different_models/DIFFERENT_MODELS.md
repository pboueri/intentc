# Different Models


# Goal
The goal of this experiment is to see the generation quality between different models and their different modes. 


# Variations
For Claude, that means haiku, sonnet, and opus, and for sonnet and opus, different levels of effort.


# Input 
The input is the intent specified in inputs/intent

# Task

Use intentc to build the intent file with differing agent configuration that specifies to use the different grid of models. 

# Output

## Long running raw storage
All the runs are output to here outputs/runs/{model}\_{effort}\_{timestamp}/

Store them there and make sure to not re-run if doing more analysis

## Analysis
Should be done with python scripts. 

1. Take screenshots of each application with playwright to show a visual distinction and generate a grid. If it doesnt run just make a big X
2. Generate the lines of code for each output and output a summary
3. Create an evaluation with opus 4.6 high effort of their quality and how many bugs or issues it thinks it has. Output a quality score of the implemnetation from [VERY_POOR, POOR, OK, GOOD, GREAT]

## Considerations
- Make sure that you use sub-agents to preserve the primary agent's context window
- Keep parallel builds to 3 so that agents do not burn through the token windows. 
- You may have to update the intent in intentc to accept "effort" for claude. Update the intent files and the implementation before proceeding. 