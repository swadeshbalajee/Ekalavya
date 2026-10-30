---
id: C001
title: Value and category labels drawn on top of bars or lines
triggers: data_plot, bar, chart, text_overlap, values_match
seen: 3
---
Charts failed review because numbers sat on the bars, category names were drawn across bars, and a
dashed reference line ran through a value label. Put each value label just above its bar with
label.next_to(bar, UP, buff=SMALL_BUFF); put category names below the x-axis with
next_to(axis_point, DOWN); keep bars above the axis (never let a bar extend below it). End a
reference line short of any label, or put its label at the line's end with next_to(line, RIGHT).
