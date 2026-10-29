---
name: order_status
description: How to answer "where is my order" questions.
---
# Order status

1. Look the order up with `lookup_order` using the id the customer gave.
2. Report the status in plain words: processing, shipped (with the ship date and the expected
   arrival), or delivered.
3. If the lookup returns `not_found`, ask the customer to check the id; ids look like A-1001.
4. Do not guess dates that the lookup does not return.
