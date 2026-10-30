---
id: C004
title: Indexing a point returned by get_top/get_center twice
triggers: render, execution, indexerror, typeerror, bar, chart
seen: 2
---
get_top(), get_bottom(), get_left(), get_right() and get_center() return one point [x, y, z].
Use get_top()[1] for its y value; never index it twice (get_top()[1][1]) and never index a float.
set_y() and set_x() take a single number.
