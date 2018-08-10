import os
import xml.etree.ElementTree as ET

import re
import cv2
import numpy as np
import tensorflow as tf

COLORS = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255), (255, 255, 0), (255, 0, 255)]


def load_weights(layers, weights):
    variables = {v.op.name: v for v in tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope="yolo")}
    ops = []
    read = 0
    for layer in layers:
        for variable_name in layer.variable_names:
            var = variables[variable_name]
            tokens = var.name.split("/")
            size = np.prod(var.shape.as_list())
            shape = var.shape.as_list()
            if "weights" in tokens[-1]:
                shape = [shape[3], shape[2], shape[0], shape[1]]
            value = np.reshape(weights[read: read + size], shape)
            if "weights" in tokens[-1]:
                value = np.transpose(value, (2, 3, 1, 0))
            ops.append(tf.assign(var, value, validate_shape=True))
            read += size

    print("Weights ready ({}/{} read)".format(read, len(weights)))
    if read != len(weights):
        print("(warning) read count and total count do not match. Possibly an incorrect weights file.")

    return ops


def load_image_paths(path_to_img_dir):
    return [os.path.join(os.path.abspath(path_to_img_dir), f) for f in os.listdir(path_to_img_dir)
            if any(f.lower().endswith(ext) for ext in ["jpg", "bmp", "png", "gif"])]


def parse_annotations(annotation_dir, image_dir):
    annotations = [os.path.join(os.path.abspath(annotation_dir), f) for f in os.listdir(annotation_dir)
                   if f.lower().endswith(".xml")]

    result = []
    for annotation in annotations:
        root = ET.parse(annotation).getroot()
        img_path = os.path.join(image_dir, root.find("filename").text)

        img_objects = []
        objects = root.findall("object")
        for object in objects:
            name = object.find("name").text
            bndbox = object.find("bndbox")
            x1 = int(bndbox.find("xmin").text)
            y1 = int(bndbox.find("ymin").text)
            x2 = int(bndbox.find("xmax").text)
            y2 = int(bndbox.find("ymax").text)
            img_objects.append((x1, y1, x2, y2, name))

        result.append((img_path, img_objects))
    return result


def preprocess_image(image_path, new_shape, objects=None):
    image = cv2.imread(image_path)
    if image is None:
        print("Failed to read {}".format(image_path))
        return

    image = cv2.resize(image, new_shape)
    image = image / 255.  # normalize to 0 - 1
    image = image[:, :, ::-1]  # BGR to RGB

    new_objects = None
    if objects is not None:
        new_objects = []
        for obj in objects:
            xmin = obj[0] / image.shape[1] * new_shape[1]  # xmin
            ymin = obj[1] / image.shape[0] * new_shape[0]  # ymin
            xmax = obj[2] / image.shape[1] * new_shape[1]  # xmin
            ymax = obj[3] / image.shape[0] * new_shape[0]  # ymin
            new_objects.append((xmin, ymin, xmax, ymax, obj[4]))

    return image, new_objects


def generate_test_batch(img_paths, batch_size, input_shape):
    total_batches = int(np.ceil(len(img_paths) / batch_size))

    for b in range(total_batches):
        images = []
        paths = []
        for i in range(min(batch_size, len(img_paths) - b * batch_size)):
            image, _ = preprocess_image(img_paths[b * batch_size + i], input_shape)
            images.append(np.expand_dims(image, axis=0))
            paths.append(img_paths[b * batch_size + i])
        yield np.concatenate(images, axis=0), paths


def sigmoid(x):
    return 1. / (1. + np.exp(-x))


def softmax(x):
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()


def iou_score(box1, box2):
    box1_min, box1_max = box1.get_top_left(), box1.get_bottom_right()
    box1_area = box1.w * box1.h
    box2_min, box2_max = box2.get_top_left(), box2.get_bottom_right()
    box2_area = box2.w * box2.h

    intersect_min = np.maximum(box1_min, box2_min)
    intersect_max = np.minimum(box1_max, box2_max)
    intersect_wh = np.maximum(intersect_max - intersect_min, 0)
    intersect_area = intersect_wh[0] * intersect_wh[1]
    union_area = np.maximum(box1_area + box2_area - intersect_area, 1e-8)

    return intersect_area / union_area


def non_maximum_suppression(boxes, iou_threshold):
    if len(boxes) == 0:
        return []

    boxes.sort(key=lambda box: box.prob, reverse=True)  # sort by prob(confidence)
    new_boxes = [boxes[0]]  # add first element
    for i in range(1, len(boxes)):
        overlapping = False
        for new_box in new_boxes:
            if iou_score(new_box, boxes[i]) >= iou_threshold:
                overlapping = True
                break
        if not overlapping:
            new_boxes.append(boxes[i])
    return new_boxes


def draw_boxes(path_to_img, boxes, class_names):
    image = cv2.imread(path_to_img)
    assert image is not None
    h, w = image.shape[0:2]
    for box in boxes:
        tl = box.get_top_left(h, w)
        br = box.get_bottom_right(h, w)
        tl = (int(tl[0]), int(tl[1]))
        br = (int(br[0]), int(br[1]))

        cv2.rectangle(image, tl, br, COLORS[box.class_idx % len(COLORS)], thickness=3)
        class_name = class_names[box.class_idx]
        cv2.putText(image, "{} {:.3f}".format(class_name, box.prob), (tl[0], tl[1] - 10), \
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLORS[box.class_idx % len(COLORS)], thickness=1)
    return image


def save_image(image, out_path):
    cv2.imwrite(out_path, image)


def load_checkpoint(saver, sess, checkpoint_dir):
    metas = [f for f in os.listdir(checkpoint_dir) if f.startswith("yolo")]

    if len(metas) == 0:
        return 0

    metas.sort(reverse=True)
    checkpoint_path = os.path.join(checkpoint_dir, metas[0])
    name = os.path.splitext(checkpoint_path)[0]
    step = int(name.split("-")[1])
    saver.restore(sess, checkpoint_path)
    return step


def save_checkpoint(saver, sess, checkpoint_dir, step):
    out_path = os.path.join(checkpoint_dir, "yolo-{}.ckpt".format(step))
    saver.save(sess, out_path)
    print("Checkpoint saved to {}".format(out_path))


class BoundingBox(object):
    def __init__(self, x=0., y=0., w=0., h=0., cx=0, cy=0, class_idx=-1, prob=-1.):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.cx = cx
        self.cy = cy
        self.class_idx = class_idx
        self.prob = prob

    def get_top_left(self, h=1., w=1.):
        return np.maximum((self.x - self.w / 2.) * w, 0.), np.maximum((self.y - self.h / 2.) * h, 0.)

    def get_bottom_right(self, h=1., w=1.):
        return np.minimum((self.x + self.w / 2.) * w, w), np.minimum((self.y + self.h / 2.) * h, h)


class Yolo(object):
    def __init__(self):
        pass
