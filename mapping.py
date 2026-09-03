import cv2
import numpy as np


class VisualMapper:

    def __init__(self):

        self.previous_gray = None

        self.x = 0.0
        self.y = 0.0

        self.route = [(0.0, 0.0)]

        self.orb = cv2.ORB_create(
            nfeatures=1000
        )

        self.matcher = cv2.BFMatcher(
            cv2.NORM_HAMMING,
            crossCheck=True
        )

    def process_frame(self, frame):

        if frame is None:
            return

        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )

        # First frame
        if self.previous_gray is None:

            self.previous_gray = gray

            return

        # Find features
        keypoints1, descriptors1 = \
            self.orb.detectAndCompute(
                self.previous_gray,
                None
            )

        keypoints2, descriptors2 = \
            self.orb.detectAndCompute(
                gray,
                None
            )

        if descriptors1 is None or descriptors2 is None:

            self.previous_gray = gray
            return

        matches = self.matcher.match(
            descriptors1,
            descriptors2
        )

        matches = sorted(
            matches,
            key=lambda m: m.distance
        )

        good_matches = matches[:50]

        if len(good_matches) >= 8:

            pts1 = np.float32([
                keypoints1[m.queryIdx].pt
                for m in good_matches
            ])

            pts2 = np.float32([
                keypoints2[m.trainIdx].pt
                for m in good_matches
            ])

            matrix, mask = cv2.estimateAffinePartial2D(
                pts1,
                pts2,
                method=cv2.RANSAC
            )

            if matrix is not None:

                dx = matrix[0, 2]
                dy = matrix[1, 2]

                scale = 0.01

                self.x += dx * scale
                self.y += dy * scale

                self.route.append(
                    (self.x, self.y)
                )

        self.previous_gray = gray

    def get_route(self):

        return self.route
