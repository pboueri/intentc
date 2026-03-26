# Specificity vs. Code Variance


## Goal
The goal of this experiment is to measure how intent specificity reduces code variance. 


## Definiton of terms
Specificity: Intent files can range from extremely high level to extremely precise. We can proxy this based on how many words there are in the total corpus, as well as how many files. 

Code Variance: The genererated code can vary a lot in terms of language used, libraries used, lines of code, and final look and feel and functionality. It's very difficult to measure the "distance" between two generated projects because there are so many degrees of freedom. Some ideas on how to do so are:
    - Normalized Compression Distance is one approach -- especially if all the variables are anonymized and the text files sorted and appended
    - Pixel Edit Distance: If the project is a GUI then measuring the difference in the images is a measure of difference
    - Raw lines of code distance: How many lines of code were generated, or number of methods, or cycolmatic complexity etc. 
    None of these are satisfying by themselves but are "proxies" for how far away two code bases are


## Final Deliverables

- an output/ folder with all the generated srcs in a particular structure:
    - runs/ -- the output of all the intentc commands that generated each folder
        - specificity_n/ -- the higher N implies more specificity
            - intent/ -- the intent files generated
            - src_{timestamp}/ -- where {timestamp} indicates the generated version
    - analysis_script.py -- an analysis script that generates the various measures of code variance that are tractable. 
        - If edits are needed here, there's no needed to regenerate the specificity_n
    - analysis/
        - specificity_vs_ncd
        - specificity_vs_pixel_edit
        - specificity_vs_raw_lines_of_code

## Inputs

These are stored in experiments/specification_sensitivity/inputs/

The project that we are going to consider will be a two-dimensional zoo builder game. The idea is that you are a zoo tycoon whose goal is to raise as many animals as possible.

### Constraints
- All the projects will share the same high-level target language to simplify analysis. In this case it will be javascript + html
- All the projects will be generated with the same model configuration and version of intentc
- All the projects will be generated from a clean slate tmp/ directory similar to the bootstrap.sh script

### Levels of Specificity

- 1: A one line description in project.ic, implementation.ic and feature.ic about the goal. 
- 2: An enriched version of project.ic, implementation.ic and feature.ic where there are several paragraphs. A validation file is added. Very little code details
- 3: A further enriched version of project.ic, implementation.ic and 3 features with validation. that layer on complexity. Still very little code details
- 4: More features (say 10) are added and code details are added with validations. Some high level description of look and feel
- 5: Code details are dominant in the spec, and visual items are brought in as references, style guides are made explicit and clear


## Execution

Large one time cost:
- Generate the input intent folders and commit them as part of the experiment -- check with the user they are what we are looking for. 
- Run each intent 5 times with the tmp approach and copy them all to the `runs/` folder. timestamp each one too. 

Iterative Refinement
- After all the runs are done create an analysis script and generate the relevant plots and things -- use your best judgement to get the output. If you did not finish we should be able to iterate here without re-running the above. 


Guidance:
- Make sure that you use sub-agents to preserve the primary agent's context window
- Keep parallel builds to 3 so that agents do not burn through the token windows. 