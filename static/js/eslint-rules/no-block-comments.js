// Custom ESLint rule: no-block-comments
// This project uses // comments exclusively. See docs/agents.md for rationale.

export default {
    meta: {
        type: 'suggestion',
        docs: {
            description: 'Disallow block comments (/* */ and /** */)',
            category: 'Stylistic Issues',
            recommended: true,
            url: 'https://github.com/user/movie-searcher/blob/main/docs/agents.md#code-comments'
        },
        messages: {
            noBlockComments: 
                'Block comments (/* */) are not allowed. Use // comments instead. ' +
                'See docs/agents.md "Code Comments" section for rationale: ' +
                'comments should explain WHY, not WHAT. Function names should be self-documenting.'
        },
        schema: []
    },
    create(context) {
        const sourceCode = context.getSourceCode();
        
        return {
            Program() {
                const comments = sourceCode.getAllComments();
                
                for (const comment of comments) {
                    if (comment.type === 'Block') {
                        context.report({
                            loc: comment.loc,
                            messageId: 'noBlockComments'
                        });
                    }
                }
            }
        };
    }
};

